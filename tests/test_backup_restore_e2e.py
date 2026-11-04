"""RUNTIME R10.1 실제 왕복: 표준 restic rest-server(TLS·기본 인증) 컨테이너에 backup 잡이 자기 영역 게이트웨이 출구(CONNECT 터널)로 보내고,
스냅숏을 새 데이터베이스에 복구해 행을 확인한다. 실제 PostgreSQL(TEST_DATABASE_URL)과 docker 가 필요하다(없으면 SKIP).
restic 은 공식 restic/restic 이미지를 래퍼 스크립트로 부른다(이 PC 에는 restic 바이너리가 없다; 제품 이미지는 apt 의 restic 을 쓰고 스모크가 실행을 확인한다).
정리는 이 시험이 띄운 이름의 컨테이너·스키마·스레드만 한다."""
import contextlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import threading
import uuid
from pathlib import Path

import pytest

from conftest import example_text, pg_schema
from helpers import AUTHOR, HANG_GUARD_SECONDS, publish
from question_authoring.wiring import backup_flow
from question_authoring.wiring.service import AuthoringService
from question_authoring.wiring.stores import PostgresQuestionStore

DOCKER = shutil.which("docker")
HOST_NAME = "backup-store"          # 프록시 우회(Go 는 localhost·loopback 주소로는 HTTPS_PROXY 를 쓰지 않는다)를 피하려는 서비스 이름


def sh(*a, check=True, timeout=120, **k):
    return subprocess.run(a, capture_output=True, text=True, check=check, timeout=timeout, **k)


class ConnectProxy:
    """자기 영역 게이트웨이 출구의 대역: CONNECT 줄을 기록하고 이름을 loopback 으로 풀어 터널을 연다(허용 목록: backup-store 하나)."""
    def __init__(self, port_of_store):
        self.lines, self.port_of_store = [], port_of_store
        self.sock = socket.socket()
        self.sock.bind(("0.0.0.0", 0))      # 컨테이너가 host-gateway 로 닿는다(이 환경의 docker 엔진은 호스트 loopback 을 공유하지 않는다)
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self.stop:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        with c:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = c.recv(4096)
                if not chunk:
                    return
                data += chunk
            line = data.split(b"\r\n", 1)[0].decode()
            self.lines.append(line)
            m = re.match(r"CONNECT ([\w.-]+):(\d+) HTTP/1\.1", line)
            if not m or m.group(1) != HOST_NAME:
                c.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                return
            up = socket.create_connection(("127.0.0.1", self.port_of_store), timeout=30)
            c.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")

            def pipe(a, b):
                try:
                    while True:
                        d = a.recv(65536)
                        if not d:
                            break
                        b.sendall(d)
                except OSError:
                    pass
                finally:
                    with contextlib.suppress(OSError):
                        b.shutdown(socket.SHUT_WR)
            t = threading.Thread(target=pipe, args=(up, c), daemon=True)
            t.start()
            pipe(c, up)
            t.join(30)
            up.close()

    def close(self):
        self.stop = True
        self.sock.close()


@pytest.fixture(scope="module")
def store_env(tmp_path_factory):
    if DOCKER is None or subprocess.run([DOCKER, "info"], capture_output=True).returncode != 0:
        pytest.skip("docker 데몬 없음")
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL 없음: PostgreSQL 시험 건너뜀")
    d = tmp_path_factory.mktemp("backup-e2e")
    d.chmod(0o755)
    sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "root.key", "-out", "root.crt", "-days", "1", "-subj", "/CN=test-root",
       "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign", cwd=d)
    sh("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", "srv.key", "-out", "srv.csr", "-subj", f"/CN={HOST_NAME}", cwd=d)
    (d / "ext.cnf").write_text(f"subjectAltName=DNS:{HOST_NAME}\nbasicConstraints=CA:FALSE\n")
    sh("openssl", "x509", "-req", "-in", "srv.csr", "-CA", "root.crt", "-CAkey", "root.key", "-CAcreateserial", "-out", "srv.crt", "-days", "1", "-extfile", "ext.cnf", cwd=d)
    pw = uuid.uuid4().hex
    d.chmod(0o777)       # htpasswd 를 컨테이너가 쓴다
    sh(DOCKER, "run", "--rm", "-v", f"{d}:/certs", "--entrypoint", "htpasswd", "restic/rest-server:latest", "-B", "-b", "-c", "/certs/htpasswd", "backup-user", pw)   # rest-server 의 표준 사용자 생성 도구(bcrypt)
    for f in d.iterdir():
        with contextlib.suppress(PermissionError):      # htpasswd 는 컨테이너(root)가 만들었다: 이미 읽을 수 있다
            f.chmod(0o644)
    name = f"qa-e2e-rest-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    sh(DOCKER, "run", "-d", "--name", name, "-p", "127.0.0.1::8000", "-v", f"{d}:/certs:ro", "-e", "PASSWORD_FILE=/certs/htpasswd",
       "-e", "OPTIONS=--tls --tls-cert /certs/srv.crt --tls-key /certs/srv.key", "restic/rest-server:latest")
    try:
        port = int(sh(DOCKER, "port", name, "8000/tcp").stdout.split(":")[-1])
        for _ in range(100):                                   # 매달림 감시: 서버가 뜰 때까지(조건)
            if socket.socket().connect_ex(("127.0.0.1", port)) == 0:
                break
            import time; time.sleep(0.1)
        (d / "repo-password").write_text("repo-pass-" + uuid.uuid4().hex)
        (d / "repo-password").chmod(0o644)
        wrapper = d / "restic"
        wrapper.write_text(f"""#!/bin/sh
exec {DOCKER} run --rm -i --add-host host.docker.internal:host-gateway -v {d}:{d}:ro \\
  -e RESTIC_REPOSITORY -e RESTIC_PASSWORD_FILE -e RESTIC_REST_USERNAME -e RESTIC_REST_PASSWORD -e HTTPS_PROXY -e SSL_CERT_FILE -e RESTIC_CACHE_DIR -e HOME \\
  restic/restic:latest "$@"
""")
        wrapper.chmod(0o755)
        proxy = ConnectProxy(port)
        yield {"dir": d, "port": port, "pw": pw, "proxy": proxy, "proxy_url": f"http://host.docker.internal:{proxy.port}", "wrapper": str(wrapper), "name": name}
        proxy.close()
    finally:
        subprocess.run([DOCKER, "rm", "-f", name], capture_output=True, timeout=60)         # 이 시험이 띄운 이름만


def env_for(se, url_with_schema, tmp):
    d = se["dir"]
    cred = d / "cred"
    cred.write_text(f"backup-user:{se['pw']}")
    cred.chmod(0o644)
    dbf = d / f"dburl-{uuid.uuid4().hex[:6]}"
    dbf.write_text(url_with_schema)
    dbf.chmod(0o644)
    return {"OPENIT_BACKUP_URL": f"https://{HOST_NAME}:{se['port']}/question-authoring", "OPENIT_BACKUP_CREDENTIALS_FILE": str(cred),
            "OPENIT_BACKUP_REPO_PASSWORD_FILE": str(d / "repo-password"), "QA_DATABASE_URL_FILE": str(dbf),
            "SSL_CERT_FILE": str(d / "root.crt"), "PATH": os.environ["PATH"]}


def with_schema(url, schema):
    import psycopg.conninfo
    p = psycopg.conninfo.conninfo_to_dict(url)
    p["options"] = f"-c search_path={schema}"
    return psycopg.conninfo.make_conninfo(**p)


def restic(se, env, *argv, stdin=None):
    child = {"PATH": env["PATH"], "HOME": "/tmp", "RESTIC_CACHE_DIR": "/tmp/restic-cache", "RESTIC_REPOSITORY": "rest:" + env["OPENIT_BACKUP_URL"],
             "RESTIC_PASSWORD_FILE": env["OPENIT_BACKUP_REPO_PASSWORD_FILE"], "RESTIC_REST_USERNAME": "backup-user", "RESTIC_REST_PASSWORD": se["pw"],
             "HTTPS_PROXY": se["proxy_url"], "SSL_CERT_FILE": env["SSL_CERT_FILE"]}
    return subprocess.run([se["wrapper"], *argv], capture_output=True, env=child, timeout=int(os.environ.get("RESTIC_DBG_TIMEOUT", HANG_GUARD_SECONDS)), stdin=stdin)


def test_backup_job_then_restore_job_through_a_real_rest_server_and_connect_tunnel(store_env, validators):
    """backup 잡(R10.1) → 스냅숏 → restore 잡 → 새 데이터베이스에서 행·해시·순번이 맞다. 보존(forget --prune)도 실제로 돈다."""
    from boundary_contracts.errors import BoundaryError
    se = store_env
    proxy = se["proxy_url"]
    with pg_schema() as (url, schema), pg_schema() as (url2, schema2):
        src = PostgresQuestionStore(url, schema=schema)
        try:
            svc = AuthoringService(src)
            publish(svc, "qs-bk", reviewed=False)
            publish(svc, "qs-bk", text=example_text("qs-bk").replace("체온".encode(), "혈압".encode()), reviewed=False)
            want = {r["version"]: r["content_hash"] for r in src.reader().list_versions("qs-bk")[0]}
            assert set(want) == {"1", "2"}
            env = env_for(se, with_schema(url, schema), None)
            before = len(se["proxy"].lines)
            out = backup_flow.run(env, proxy=proxy, restic=se["wrapper"])
            assert out["bytes"] > 0 and re.fullmatch(r"[0-9a-f]{8,64}", out["snapshot_id"]), out
            out2 = backup_flow.run(env, proxy=proxy, restic=se["wrapper"])               # 두 번째 백업: 보존 정책이 돌아도 최신은 남는다
            lines = se["proxy"].lines[before:]
            assert lines and all(l == f"CONNECT {HOST_NAME}:{se['port']} HTTP/1.1" for l in lines), lines      # R1.8: 출구 터널로만 나갔다
            snaps = json.loads(restic(se, env, "snapshots", "--json").stdout)
            ids = {s["id"] for s in snaps}
            assert out2["snapshot_id"] in {i[:len(out2["snapshot_id"])] for i in ids} and all(s["tags"] == ["question-authoring"] for s in snaps)

            # restore 잡: 빈 데이터베이스에 latest 를 되돌린다
            env2 = env_for(se, with_schema(url2, schema2), None)
            res = backup_flow.restore(env2, "latest", proxy=proxy, restic=se["wrapper"])
            assert res["bytes"] > 0 and any(i.startswith(res["snapshot_id"]) or res["snapshot_id"].startswith(i[:8]) for i in ids)
            dst = PostgresQuestionStore(url2, schema=schema2)
            try:
                got = {r["version"]: r["content_hash"] for r in dst.reader().list_versions("qs-bk")[0]}
                assert got == want                                                    # 행이 그대로 돌아왔다
                for r in dst.reader().list_versions("qs-bk")[0]:
                    assert dst.reader().get_question_bank(r)["question_set_id"] == "qs-bk"      # 해시 재계산 포함 읽기
                assert dst.reader().latest("qs-bk")["version"] == "2"
                # 순번이 맞춰졌다: 복구한 데이터베이스에서 새 버전을 계속 만들 수 있다(BIGSERIAL 이 COPY 로 올라가지 않는 문제)
                svc2 = AuthoringService(dst)
                ref3 = publish(svc2, "qs-bk", text=example_text("qs-bk").replace("체온".encode(), "맥박".encode()), reviewed=False)
                assert ref3["version"] == "3" and len(dst.reader().get_reviews(ref3)) == 1
            finally:
                dst.close()
            # 비어 있지 않은 데이터베이스에는 다시 복구하지 않는다(덮어쓰기 금지)
            with pytest.raises(BoundaryError) as e:
                backup_flow.restore(env2, "latest", proxy=proxy, restic=se["wrapper"])
            assert e.value.code == "FAILED_PRECONDITION"
            # 스냅숏 ID 를 지정한 복구도 된다(세 번째 빈 데이터베이스)
            with pg_schema() as (url3, schema3):
                s3 = PostgresQuestionStore(url3, schema=schema3); s3.close()
                res3 = backup_flow.restore(env_for(se, with_schema(url3, schema3), None), out["snapshot_id"] if any(i.startswith(out["snapshot_id"]) for i in ids) else "latest",
                                           proxy=proxy, restic=se["wrapper"])
                assert res3["bytes"] > 0
        finally:
            src.close()


def test_wrong_repository_credentials_fail_as_unavailable_without_leaking(store_env):
    from boundary_contracts.errors import BoundaryError
    se = store_env
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema)
        try:
            env = env_for(se, with_schema(url, schema), None)
            Path(env["OPENIT_BACKUP_CREDENTIALS_FILE"]).write_text("backup-user:wrong-password")
            with pytest.raises(BoundaryError) as e:
                backup_flow.run(env, proxy=se["proxy_url"], restic=se["wrapper"])
            assert e.value.code == "UNAVAILABLE" and "wrong-password" not in e.value.message and se["pw"] not in e.value.message
        finally:
            s.close()
