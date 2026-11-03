"""백업 잡(RUNTIME R10.1): 이 영역의 PostgreSQL 데이터를 논리 덤프(COPY 텍스트)로 꺼내 restic 으로 platform 백업 저장소(restic REST, HTTPS)에 보낸다.

왜 psycopg COPY 인가: pg_dump 는 서버보다 낮은 클라이언트 버전을 거부해 이미지가 서버 버전에 묶이고, 이 영역의 표는 다섯뿐이다.
스키마는 마이그레이션이 만들므로 데이터만 낸다(복구 = 마이그레이션 뒤 COPY 로 되돌림, docs/runbook.md). 덤프는 REPEATABLE READ 읽기 전용 트랜잭션 하나라 한 시점의 일관된 모습이다.
영역 밖으로 나가는 길은 자기 영역 게이트웨이 출구(OPENIT_EGRESS_PROXY)뿐이다: restic(Go)은 HTTPS_PROXY 의 CONNECT 터널을 쓴다(RUNTIME R1.8).
비밀(저장소 자격·암호)은 파일에서만 읽고 어떤 출력에도 쓰지 않는다(R6.3). 하위 프로세스에는 필요한 환경 변수만 명시해 넘긴다(R9.1).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Callable, Mapping
from urllib.parse import urlsplit

from boundary_contracts import read_secret
from boundary_contracts.clients import egress_proxy_from, require_tls_url
from boundary_contracts.errors import BoundaryError

# 표 이름의 정본은 migrations/postgres 이고 tests/test_backup_flow.py 가 일치를 본다. 스키마 순서(FK 없음)대로.
TABLES = ("versions", "reviews", "changes", "drafts", "blobs")
# 덤프 문장 상한(초): 표 전체를 읽는다. 잡 timeout_seconds(deploy-jobs.json)와 같은 값. 측정하지 않은 시작값(ADR 0029).
DUMP_STATEMENT_SECONDS = 1800


def dump(url: str, out) -> int:
    """TABLES 를 한 시점의 일관된 모습으로 out(바이너리 파일 객체)에 쓴다. 쓴 바이트 수."""
    import psycopg
    import psycopg.conninfo
    p = psycopg.conninfo.conninfo_to_dict(url)
    p["connect_timeout"] = 3
    p["options"] = (p.get("options", "") + f" -c statement_timeout={DUMP_STATEMENT_SECONDS * 1000}").strip()
    n = 0
    conn = psycopg.connect(autocommit=False, **p)
    try:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        for t in TABLES:
            head = f"COPY {t} FROM STDIN;\n".encode()
            out.write(head)
            n += len(head)
            with conn.cursor().copy(f"COPY {t} TO STDOUT") as cp:
                for chunk in cp:
                    out.write(bytes(chunk))
                    n += len(chunk)
            out.write(b"\\.\n\n")
            n += 4
    finally:
        conn.close()
    return n


def run(env: Mapping[str, str], *, restic: str = "restic", runner: Callable = subprocess.run) -> dict:
    """영역의 PostgreSQL 을 백업한다. 설정이 없으면 시작하지 않는다. 실패는 BoundaryError 이고 메시지에 비밀·접속 문자열이 없다."""
    url = env.get("OPENIT_BACKUP_URL")
    cred = read_secret("OPENIT_BACKUP_CREDENTIALS", environ=env)
    pw_file = env.get("OPENIT_BACKUP_REPO_PASSWORD_FILE")
    if not url or not cred or not pw_file:
        raise BoundaryError("INVALID_RECORD", "OPENIT_BACKUP_URL, OPENIT_BACKUP_CREDENTIALS_FILE, OPENIT_BACKUP_REPO_PASSWORD_FILE 가 필요하다")
    require_tls_url(url)
    if urlsplit(url).scheme != "https":
        raise BoundaryError("INVALID_RECORD", "OPENIT_BACKUP_URL 은 https 여야 한다")
    user, sep, secret = cred.strip().partition(":")
    if not sep or not user:
        raise BoundaryError("INVALID_RECORD", "OPENIT_BACKUP_CREDENTIALS_FILE 내용은 '<사용자>:<암호>' 한 줄이어야 한다")
    pg = read_secret("QA_DATABASE_URL", environ=env)
    if not pg:
        raise BoundaryError("INVALID_RECORD", "백업할 PostgreSQL 이 없다(QA_DATABASE_URL_FILE). SQLite 는 개발용이라 백업 대상이 아니다")
    # 자격은 restic 의 표준 환경 변수(RESTIC_REST_USERNAME·PASSWORD)로만 하위 프로세스에 간다: argv·저장소 주소에 넣지 않는다
    child = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": "/tmp", "RESTIC_CACHE_DIR": "/tmp/restic-cache",
             "RESTIC_REPOSITORY": "rest:" + url, "RESTIC_PASSWORD_FILE": pw_file,
             "RESTIC_REST_USERNAME": user, "RESTIC_REST_PASSWORD": secret}
    proxy = egress_proxy_from(env)
    if proxy:
        child["HTTPS_PROXY"] = proxy
    if env.get("SSL_CERT_FILE"):
        child["SSL_CERT_FILE"] = env["SSL_CERT_FILE"]

    def call(*argv, stdin=None) -> int:
        try:
            r = runner([restic, *argv], stdin=stdin, env=child, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=DUMP_STATEMENT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as e:       # restic 이 없거나 시간 초과: 종류만 낸다
            raise BoundaryError("UNAVAILABLE", f"restic 실행 실패({type(e).__name__})") from None
        return r.returncode

    if call("cat", "config") != 0 and call("init") != 0:
        raise BoundaryError("UNAVAILABLE", "백업 저장소를 열거나 만들지 못했다")
    with tempfile.TemporaryFile() as f:
        try:
            total = dump(pg, f)
        except Exception as e:                                   # noqa: BLE001  접속 문자열이 섞일 수 있어 종류만 낸다
            raise BoundaryError("UNAVAILABLE", f"데이터베이스를 덤프하지 못했다({type(e).__name__})") from None
        f.seek(0)
        if call("backup", "--stdin", "--stdin-filename", "question-authoring.copy", "--tag", "question-authoring", "--host", "question-authoring", stdin=f) != 0:
            raise BoundaryError("UNAVAILABLE", "백업 저장소로 보내지 못했다")
    return {"backed_up": ["question-authoring"], "bytes": total}
