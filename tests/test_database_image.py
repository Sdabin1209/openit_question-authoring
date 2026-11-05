"""D126 영역 DB 이미지(question-authoring-db): 선언(라벨 io.openit.database)대로 계정·권한·접속 규칙·TLS 가 이미지 안에서 만들어지는지 실제 컨테이너로 확인한다.
모든 접속은 사용자 정의 docker 네트워크 안에서 컨테이너 이름으로(호스트 포트 없음), verify-full 로 한다. 정리는 이 시험이 만든 이름만. docker 가 없으면 SKIP."""
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest
from boundary_contracts import load_schema, validator

from helpers import HANG_GUARD_SECONDS, image

DOCKER = shutil.which("docker")
ROOT = Path(__file__).resolve().parent.parent
PG = image("POSTGRES_IMAGE")            # digest 고정(images.env)
PWS = {"qa": "owner-pass-1", "qa_app": "dml-pass-2"}
# 시험용 선언: 제품 선언(owner 하나)에 dml 계정을 더한 것. 이미지 초기화 코드가 dml 경로도 맞게 하는지 보려고 /etc/openit/database.json 위에 덮어 마운트한다
TEST_DECL = {"engine": "postgresql", "major": 16, "port": 5432, "accounts": [
    {"name": "qa", "database": "qa", "privilege": "owner", "url_env": ["QA_DATABASE_URL_FILE"]},
    {"name": "qa_app", "database": "qa", "privilege": "dml", "url_env": ["QA_DATABASE_URL_FILE"]}]}


def sh(*a, check=True, timeout=HANG_GUARD_SECONDS, **k):
    return subprocess.run(a, capture_output=True, text=True, check=check, timeout=timeout, **k)


@pytest.fixture(scope="module")
def db_image():
    if DOCKER is None or subprocess.run([DOCKER, "info"], capture_output=True).returncode != 0:
        pytest.skip("docker 데몬 없음")
    tag = f"question-authoring-db:test-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    env = dict(os.environ, VARIANT="db")
    env.setdefault("GIT_CONFIG_GLOBAL", str(ROOT.parent / "head" / "shared" / "gitconfig-local-mirrors"))
    r = subprocess.run(["bash", str(ROOT / "scripts" / "build-local-image.sh"), tag], capture_output=True, text=True, env=env, timeout=HANG_GUARD_SECONDS)
    assert r.returncode == 0, r.stderr[-800:]
    yield tag, json.loads(r.stdout.strip().splitlines()[-1])
    subprocess.run([DOCKER, "rmi", tag], capture_output=True, timeout=120)


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    d = tmp_path_factory.mktemp("dbpki")
    return d


def make_certs(d, host):
    sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "root.key", "-out", "root.crt", "-days", "1", "-subj", "/CN=test-root",
       "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign", cwd=d)
    sh("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", "tls.key", "-out", "srv.csr", "-subj", f"/CN={host}", cwd=d)
    (d / "ext.cnf").write_text(f"subjectAltName=DNS:{host}\nbasicConstraints=CA:FALSE\n")
    sh("openssl", "x509", "-req", "-in", "srv.csr", "-CA", "root.crt", "-CAkey", "root.key", "-CAcreateserial", "-out", "tls.crt", "-days", "1", "-extfile", "ext.cnf", cwd=d)


class Stack:
    """한 번의 DB 컨테이너(이름·네트워크·비밀 폴더). 클라이언트는 같은 네트워크의 postgres 이미지 psql 이다."""
    def __init__(self, tag, tmp, decl=None, mount_init=None, tls=True, extra_cmd=()):
        u = uuid.uuid4().hex[:8]
        self.net, self.name, self.tmp = f"qa-db-net-{u}", f"qa-db-{u}", tmp
        (tmp / "certs").mkdir()
        make_certs(tmp / "certs", self.name)
        (tmp / "secrets").mkdir()
        (tmp / "su-pass").write_text("super-pass-0")
        for n, pw in PWS.items():
            (tmp / "secrets" / n).write_text(pw)
        for p in list((tmp / "certs").iterdir()) + list((tmp / "secrets").iterdir()) + [tmp / "su-pass"]:
            p.chmod(0o644)
        for d in (tmp, tmp / "certs", tmp / "secrets"):
            d.chmod(0o755)
        if not tls:
            for f in ("tls.crt", "tls.key"):
                (tmp / "certs" / f).unlink()
        args = ["-v", f"{tmp}/certs:/certs:ro", "-v", f"{tmp}/secrets:/run/secrets/db:ro", "-v", f"{tmp}/su-pass:/run/secrets/su:ro",
                "-e", "POSTGRES_PASSWORD_FILE=/run/secrets/su"]
        if decl is not None:
            (tmp / "decl.json").write_text(json.dumps(decl))
            (tmp / "decl.json").chmod(0o644)
            args += ["-v", f"{tmp}/decl.json:/etc/openit/database.json:ro"]
        if mount_init is not None:
            mount_init.chmod(0o755)
            args += ["-v", f"{mount_init}:/docker-entrypoint-initdb.d/10-accounts.sh:ro"]
        sh(DOCKER, "network", "create", self.net)
        sh(DOCKER, "run", "-d", "--name", self.name, "--network", self.net, *args, tag, *(["postgres", *extra_cmd] if extra_cmd else []))

    def wait_ready(self):
        end = time.monotonic() + 90
        while time.monotonic() < end:
            if sh(DOCKER, "exec", self.name, "pg_isready", "-U", "postgres", check=False).returncode == 0 and \
                    "init process complete" in sh(DOCKER, "logs", self.name, check=False).stderr + sh(DOCKER, "logs", self.name, check=False).stdout:
                time.sleep(0.5)
                return True
            if sh(DOCKER, "inspect", "-f", "{{.State.Running}}", self.name, check=False).stdout.strip() != "true":
                return False
            time.sleep(0.5)
        return False

    def psql(self, user, sql, sslmode="verify-full", dbname="qa", pw=None):
        r = sh(DOCKER, "run", "--rm", "--network", self.net, "-v", f"{self.tmp}/certs:/c:ro", "-e", f"PGPASSWORD={pw if pw is not None else PWS.get(user, '')}",
               "--entrypoint", "psql", PG, f"host={self.name} dbname={dbname} user={user} sslmode={sslmode} sslrootcert=/c/root.crt", "-X", "-At", "-c", sql, check=False)
        return r.returncode, r.stdout.strip(), r.stderr.strip()

    def close(self):
        subprocess.run([DOCKER, "rm", "-f", self.name], capture_output=True)   # 왜 timeout 없음: 정리는 데몬이 끝을 정한다(우리 코드가 아니다)
        subprocess.run([DOCKER, "network", "rm", self.net], capture_output=True)


@pytest.fixture
def stack(db_image, tmp_path):
    made = []

    def make(**kw):
        d = tmp_path / f"s{len(made)}"
        d.mkdir()
        s = Stack(db_image[0], d, **kw)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


def test_label_is_the_declaration_and_every_url_env_is_a_name_of_this_areas_env(db_image):
    tag, result = db_image
    decl = json.loads(result["labels"]["io.openit.database"])
    assert not list(validator("DatabaseDeclaration").iter_errors(decl))
    assert decl == json.loads((ROOT / "db" / "database.json").read_text(encoding="utf-8"))
    env = load_schema("QuestionAuthoringEnv")["properties"]
    assert all(n in env for a in decl["accounts"] for n in a["url_env"])
    assert result["labels"]["io.openit.service"] == "question-authoring-db"


def test_label_set_passes_image_labels_contract(db_image):
    """3.4.11 에서 ImageLabels 가 io.openit.database 를 허용한다(3.4.10 은 누락). 그 전 판에서는 이 시험이 실패해 고정 올림을 알려 준다."""
    errs = list(validator("ImageLabels").iter_errors(db_image[1]["labels"]))
    assert errs == [], errs[:1]


def test_owner_connects_with_verify_full_and_flags_are_least_privilege(stack):
    s = stack()
    assert s.wait_ready()
    rc, out, err = s.psql("qa", "SELECT current_user, ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
    assert (rc, out) == (0, "qa|t"), (out, err)                                                 # verify-full 로 맺은 TLS 연결
    rc, out, _ = s.psql("qa", "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolcanlogin, rolconnlimit FROM pg_roles WHERE rolname='qa'")
    assert out == "f|f|f|f|t|40"
    rc, out, _ = s.psql("qa", "CREATE TABLE t(id serial PRIMARY KEY, v text); INSERT INTO t(v) VALUES ('a'); SELECT count(*) FROM t")
    assert rc == 0 and out.endswith("1")                                                        # owner 는 자기 DB 에서 DDL 과 DML
    rc, out, _ = s.psql("qa", "SELECT datdba::regrole::text FROM pg_database WHERE datname='qa'")
    assert out == "qa"                                                                          # owner 가 데이터베이스를 소유한다


def test_wrong_password_plain_connection_and_public_connect_are_refused(stack):
    s = stack()
    assert s.wait_ready()
    assert s.psql("qa", "SELECT 1", pw="wrong")[0] != 0
    rc, _, err = s.psql("qa", "SELECT 1", sslmode="disable")
    assert rc != 0 and "pg_hba.conf rejects connection" in err and "no encryption" in err, err      # 평문 접속은 거절된다
    rc, _, err = s.psql("postgres", "SELECT 1", pw="super-pass-0")
    assert rc != 0 and "pg_hba.conf rejects" in err, err                                        # 슈퍼유저는 맞는 암호로도 네트워크 접속 불가
    rc, out, _ = s.psql("qa", "SELECT count(*) FROM pg_database d, aclexplode(d.datacl) a WHERE d.datname='qa' AND a.grantee = 0")
    assert (rc, out) == (0, "0")                                                                # PUBLIC(grantee 0)에 데이터베이스 권한이 없다(CONNECT 회수)


def test_dml_account_can_read_write_but_not_ddl_and_defaults_cover_new_tables(stack):
    s = stack(decl=TEST_DECL)
    assert s.wait_ready()
    assert s.psql("qa", "CREATE TABLE t1(id serial PRIMARY KEY, v text)")[0] == 0
    rc, out, err = s.psql("qa_app", "INSERT INTO t1(v) VALUES ('x'); UPDATE t1 SET v='y'; SELECT v FROM t1; DELETE FROM t1")
    assert rc == 0, err                                                                         # 기본 권한: 나중에 만든 표·시퀀스(serial)에도 DML
    assert s.psql("qa", "CREATE TABLE t2(id int)")[0] == 0
    assert s.psql("qa_app", "SELECT count(*) FROM t2")[0] == 0
    for ddl in ("CREATE TABLE evil(id int)", "DROP TABLE t1", "ALTER TABLE t1 ADD COLUMN c int", "TRUNCATE t1"):
        rc, _, err = s.psql("qa_app", ddl)
        assert rc != 0 and ("permission denied" in err or "must be owner" in err), (ddl, err)     # dml 계정은 스키마 변경 불가
    rc, out, _ = s.psql("qa", "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolconnlimit FROM pg_roles WHERE rolname='qa_app'")
    assert out == "f|f|f|f|8"


def test_mutation_granting_create_to_the_dml_account_is_caught(stack, tmp_path):
    """변이 확인: 초기화 스크립트가 dml 계정에 스키마 CREATE 를 주면 'dml 은 DDL 불가' 단언이 깨진다(시험이 실제로 권한을 본다는 증거)."""
    mutated = tmp_path / "mut.sh"
    src = (ROOT / "db" / "initdb" / "10-accounts.sh").read_text(encoding="utf-8")
    mutated.write_text(src.replace("SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'a') \\gexec",
                                   "SELECT format('GRANT USAGE, CREATE ON SCHEMA public TO %I', :'a') \\gexec"), encoding="utf-8")
    assert mutated.read_text() != src
    s = stack(decl=TEST_DECL, mount_init=mutated)
    assert s.wait_ready()
    rc, _, _ = s.psql("qa_app", "CREATE TABLE evil(id int)")
    assert rc == 0            # 변이된 이미지에서는 DDL 이 된다 → 정상 이미지의 위 시험이 의미 있다


def test_without_tls_files_the_server_does_not_start(stack):
    s = stack(tls=False)
    end = time.monotonic() + 30
    while time.monotonic() < end and sh(DOCKER, "inspect", "-f", "{{.State.Running}}", s.name, check=False).stdout.strip() == "true":
        time.sleep(0.5)
    assert sh(DOCKER, "inspect", "-f", "{{.State.ExitCode}}", s.name).stdout.strip() == "1"
    assert "TLS" in sh(DOCKER, "logs", s.name, check=False).stderr


def test_max_connections_and_scram_are_set(stack):
    s = stack()
    assert s.wait_ready()
    rc, out, _ = s.psql("qa", "SHOW max_connections; SHOW password_encryption; SHOW ssl")
    assert out.splitlines() == ["50", "scram-sha-256", "on"]


def test_renewed_server_certificate_is_picked_up_without_restart(stack, tmp_path):
    """갱신된 인증서를 스스로 다시 읽는다(step-ca 가 같은 파일을 바꾼다): 처음 인증서는 클라이언트가 믿지 않는 루트 A 가 서명했다 → verify-full 실패.
    같은 파일을 클라이언트가 믿는 루트 B 가 서명한 인증서로 바꾸면 컨테이너를 다시 띄우지 않고 접속이 된다. 이미지의 주기는 60초이므로 조건(접속 성공)이 될 때까지 기다린다."""
    s = stack()
    assert s.wait_ready()
    b = tmp_path / "rootB"
    b.mkdir()
    make_certs(b, s.name)                                   # 새 루트 B 와 그 서명 인증서(같은 이름)
    trust_b = tmp_path / "trustB"
    trust_b.mkdir()
    shutil.copy(b / "root.crt", trust_b / "root.crt")

    def connect():
        r = sh(DOCKER, "run", "--rm", "--network", s.net, "-v", f"{trust_b}:/c:ro", "-e", f"PGPASSWORD={PWS['qa']}", "--entrypoint", "psql", PG,
               f"host={s.name} dbname=qa user=qa sslmode=verify-full sslrootcert=/c/root.crt", "-X", "-At", "-c", "SELECT 1", check=False)
        return r.returncode, r.stderr
    rc, err = connect()
    assert rc != 0 and "certificate verify failed" in err, err                                  # 처음에는 믿지 않는 루트라 거부
    for f in ("tls.crt", "tls.key"):                                                           # 같은 파일 이름으로 갱신(마운트 폴더 안에서 덮어쓴다)
        data = (b / f).read_bytes()
        (s.tmp / "certs" / f).write_bytes(data)
        (s.tmp / "certs" / f).chmod(0o644)
    end = time.monotonic() + 150                                                               # 매달림 감시: 주기 60초 + 여유
    while time.monotonic() < end:
        rc, err = connect()
        if rc == 0:
            break
        time.sleep(5)
    assert rc == 0, err
    assert "다시 읽었다" in sh(DOCKER, "logs", s.name, check=False).stderr


def health_rc(s):
    r = sh(DOCKER, "exec", s.name, "qa-db-healthcheck.sh", check=False)
    return r.returncode, r.stderr


def test_image_declares_a_healthcheck_and_a_normal_server_is_healthy(db_image, stack):
    tag, _ = db_image
    hc = json.loads(sh(DOCKER, "image", "inspect", "-f", "{{json .Config.Healthcheck}}", tag).stdout)
    assert hc["Test"] == ["CMD", "qa-db-healthcheck.sh"] and hc["Interval"] > 0 and hc["Timeout"] > 0
    s = stack()
    assert s.wait_ready()
    assert health_rc(s) == (0, "")                                                              # 선언 계정이 있고 TLS 로 접속을 받는다
    end = time.monotonic() + 120                                                                # 매달림 감시: 주기 15초 + 여유. Docker 가 실제로 healthy 로 표시하는지
    st = ""
    while time.monotonic() < end:
        st = sh(DOCKER, "inspect", "-f", "{{.State.Health.Status}}", s.name).stdout.strip()
        if st == "healthy":
            break
        time.sleep(3)
    assert st == "healthy", st


def test_mutation_server_without_tls_is_unhealthy(stack, db_image):
    """변이: 서버 인자 뒤에 -c ssl=off 를 붙이면(마지막 설정이 이긴다) TLS 없이 뜬다. 서버는 살아 있고 pg_isready 는 통과하지만 점검은 unhealthy 여야 한다."""
    s = stack(extra_cmd=["-c", "ssl=off"])
    assert s.wait_ready()
    assert sh(DOCKER, "exec", s.name, "pg_isready", "-U", "postgres", check=False).returncode == 0
    rc, err = health_rc(s)
    assert rc == 1 and "TLS" in err, (rc, err)


def test_mutation_declared_account_that_cannot_login_is_unhealthy(stack):
    """변이: 선언한 계정의 LOGIN 을 서버 안에서 빼면(실제 상태를 바꾼다. 점검에 덮어쓰는 입력은 없다) 점검이 unhealthy 가 된다."""
    s = stack()
    assert s.wait_ready()
    assert health_rc(s) == (0, "")
    sh(DOCKER, "exec", "-u", "postgres", s.name, "psql", "-X", "-c", "ALTER ROLE qa NOLOGIN")
    rc, err = health_rc(s)
    assert rc == 1 and "준비되지" in err, (rc, err)


def test_without_tls_files_the_container_never_becomes_healthy(stack):
    s = stack(tls=False)
    time.sleep(8)
    assert sh(DOCKER, "inspect", "-f", "{{.State.Running}}", s.name, check=False).stdout.strip() == "false"   # 시작하지 않는 컨테이너는 healthy 가 될 수 없다
    assert sh(DOCKER, "inspect", "-f", "{{.State.Health.Status}}", s.name).stdout.strip() != "healthy"
