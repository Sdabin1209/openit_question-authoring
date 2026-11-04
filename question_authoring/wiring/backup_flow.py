"""백업 잡(RUNTIME R10.1): 이 영역의 PostgreSQL 데이터를 논리 덤프(COPY 텍스트)로 꺼내 restic 으로 platform 백업 저장소(restic REST, HTTPS)에 보낸다.

왜 psycopg COPY 인가: pg_dump 는 서버보다 낮은 클라이언트 버전을 거부해 이미지가 서버 버전에 묶이고, 이 영역의 표는 다섯뿐이다.
스키마는 마이그레이션이 만들므로 데이터만 낸다(복구 = 마이그레이션 뒤 COPY 로 되돌림, docs/runbook.md). 덤프는 REPEATABLE READ 읽기 전용 트랜잭션 하나라 한 시점의 일관된 모습이다.
영역 밖으로 나가는 길은 자기 영역 게이트웨이 출구(OPENIT_EGRESS_PROXY)뿐이다: restic(Go)은 HTTPS_PROXY 의 CONNECT 터널을 쓴다(RUNTIME R1.8).
비밀(저장소 자격·암호)은 파일에서만 읽고 어떤 출력에도 쓰지 않는다(R6.3). 하위 프로세스에는 필요한 환경 변수만 명시해 넘긴다(R9.1).
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from typing import Callable, Mapping
from urllib.parse import urlsplit

from boundary_contracts import read_secret
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


# 보존 정책(restic forget --prune): 매일 7개, 매주 4개, 매월 6개. 근거: restic 문서의 forget 예시 정책을 시작값으로 쓴다. 이 영역의 데이터 크기·복구 요구(RPO/RTO)·저장 용량은
# 측정하지 않았다(근거 기록 없음, ADR 0030). 백업 저장소는 append-only 로 쓰지 않으므로(헤드 지시) 보존은 이 잡이 지킨다.
KEEP = ("--keep-daily", "7", "--keep-weekly", "4", "--keep-monthly", "6")
TAG = "question-authoring"
_SNAP = re.compile(r"^(latest|[0-9a-f]{8,64})$")


def _session(env: Mapping[str, str], proxy, restic: str, runner: Callable):
    """백업·복구가 공유하는 설정 검사와 restic 호출기. (call, 데이터베이스 접속 문자열)."""
    url = env.get("OPENIT_BACKUP_URL")
    cred = read_secret("OPENIT_BACKUP_CREDENTIALS", environ=env)
    pw_file = env.get("OPENIT_BACKUP_REPO_PASSWORD_FILE")
    if not url or not cred or not pw_file:
        raise BoundaryError("INVALID_RECORD", "OPENIT_BACKUP_URL, OPENIT_BACKUP_CREDENTIALS_FILE, OPENIT_BACKUP_REPO_PASSWORD_FILE 가 필요하다")
    if urlsplit(url).scheme != "https":
        raise BoundaryError("INVALID_RECORD", "OPENIT_BACKUP_URL 은 https 여야 한다")
    user, sep, secret = cred.strip().partition(":")
    if not sep or not user:
        raise BoundaryError("INVALID_RECORD", "OPENIT_BACKUP_CREDENTIALS_FILE 내용은 '<사용자>:<암호>' 한 줄이어야 한다")
    pg = read_secret("QA_DATABASE_URL", environ=env)
    if not pg:
        raise BoundaryError("INVALID_RECORD", "PostgreSQL 이 없다(QA_DATABASE_URL_FILE). SQLite 는 개발용이라 백업·복구 대상이 아니다")
    # 자격은 restic 의 표준 환경 변수(RESTIC_REST_USERNAME·PASSWORD)로만 하위 프로세스에 간다: argv·저장소 주소에 넣지 않는다
    child = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": "/tmp", "RESTIC_CACHE_DIR": "/tmp/restic-cache",
             "RESTIC_REPOSITORY": "rest:" + url, "RESTIC_PASSWORD_FILE": pw_file,
             "RESTIC_REST_USERNAME": user, "RESTIC_REST_PASSWORD": secret}
    if proxy:     # 자기 영역 게이트웨이 출구(진입점이 egress_proxy_from 으로 꺼내 넘긴다: HTTP 클라이언트 모듈은 link_* 와 진입점만 쓴다, 구조 시험 3)
        child["HTTPS_PROXY"] = proxy
    if env.get("SSL_CERT_FILE"):
        child["SSL_CERT_FILE"] = env["SSL_CERT_FILE"]

    def call(*argv, stdin=None, stdout=None, capture=False):
        out = subprocess.PIPE if capture else (stdout if stdout is not None else subprocess.DEVNULL)
        try:
            r = runner([restic, *argv], stdin=stdin, env=child, stdout=out, stderr=subprocess.DEVNULL, timeout=DUMP_STATEMENT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as e:       # restic 이 없거나 시간 초과: 종류만 낸다
            raise BoundaryError("UNAVAILABLE", f"restic 실행 실패({type(e).__name__})") from None
        return (r.returncode, r.stdout or b"") if capture else r.returncode
    return call, pg


def run(env: Mapping[str, str], *, proxy: str | None = None, restic: str = "restic", runner: Callable = subprocess.run) -> dict:
    """영역의 PostgreSQL 을 백업하고 보존 정책을 적용한다. 설정이 없으면 시작하지 않는다. 실패는 BoundaryError 이고 메시지에 비밀·접속 문자열이 없다."""
    call, pg = _session(env, proxy, restic, runner)
    if call("cat", "config") != 0 and call("init") != 0:
        raise BoundaryError("UNAVAILABLE", "백업 저장소를 열거나 만들지 못했다")
    with tempfile.TemporaryFile() as f:
        try:
            total = dump(pg, f)
        except Exception as e:                                   # noqa: BLE001  접속 문자열이 섞일 수 있어 종류만 낸다
            raise BoundaryError("UNAVAILABLE", f"데이터베이스를 덤프하지 못했다({type(e).__name__})") from None
        f.seek(0)
        rc, out = call("backup", "--json", "--stdin", "--stdin-filename", "question-authoring.copy", "--tag", TAG,
                       "--host", "question-authoring", stdin=f, capture=True)
        if rc != 0:
            raise BoundaryError("UNAVAILABLE", "백업 저장소로 보내지 못했다")
    if call("forget", "--prune", "--tag", TAG, "--host", "question-authoring", *KEEP) != 0:
        raise BoundaryError("UNAVAILABLE", "보존 정책을 적용하지 못했다(forget --prune)")      # 스냅숏은 저장됐으나 보존이 지켜지지 않으면 실패로 알린다
    return {"snapshot_id": _snapshot_id(out), "bytes": total}


def _resolve(call, snapshot: str) -> str:
    """'latest' 또는 스냅숏 ID → 스냅숏 ID. 이 영역의 스냅숏(태그·호스트)만 본다."""
    import json
    if not _SNAP.match(snapshot):
        raise BoundaryError("INVALID_RECORD", "스냅숏은 'latest' 또는 16진 ID 여야 한다")
    rc, out = call("snapshots", "--json", "--tag", TAG, "--host", "question-authoring", capture=True)
    if rc != 0:
        raise BoundaryError("UNAVAILABLE", "백업 저장소의 스냅숏 목록을 읽지 못했다")
    try:
        snaps = sorted(json.loads(out or b"[]"), key=lambda x: x["time"])
    except (ValueError, KeyError, TypeError):
        raise BoundaryError("UNAVAILABLE", "스냅숏 목록을 읽지 못했다") from None
    if snapshot == "latest":
        if not snaps:
            raise BoundaryError("NOT_FOUND", "복구할 스냅숏이 없다")
        return snaps[-1]["id"]
    hit = [x["id"] for x in snaps if x["id"].startswith(snapshot)]
    if len(hit) != 1:
        raise BoundaryError("NOT_FOUND", "그 스냅숏이 없거나 둘 이상과 맞는다")
    return hit[0]


# 종료 줄 `\.` 는 줄 맨 앞에서만 인정한다: COPY 텍스트는 값 안의 역슬래시를 `\\` 로 내보내므로 `...\\.` 끝이 종료로 오인되면 행이 잘린다(실제 왕복 시험에서 드러났다)
_BLOCK = re.compile(rb"COPY (\w+) FROM STDIN;\n(.*?)^\\\.\n", re.S | re.M)


def restore(env: Mapping[str, str], snapshot: str = "latest", *, proxy: str | None = None, restic: str = "restic", runner: Callable = subprocess.run) -> dict:
    """스냅숏을 빈 데이터베이스에 되돌린다(RUNTIME R10.1, DATA_OWNERSHIP 백업 행: 복구는 그 영역의 복구 잡).
    비어 있지 않은 데이터베이스는 거절한다(덮어쓰기 금지: 지운 뒤 다시 하는 것은 사람의 결정). 스키마는 마이그레이션이 만든다. 한 트랜잭션이라 중간 실패는 흔적을 남기지 않는다."""
    import psycopg
    import psycopg.conninfo
    call, pg = _session(env, proxy, restic, runner)
    sid = _resolve(call, snapshot)
    from .stores import PostgresQuestionStore
    try:
        PostgresQuestionStore(pg).close()                         # 마이그레이션으로 표를 만든다(이미 있으면 그대로)
    except Exception as e:                                        # noqa: BLE001  접속 문자열이 섞일 수 있어 종류만 낸다
        raise BoundaryError("UNAVAILABLE", f"데이터베이스를 열지 못했다({type(e).__name__})") from None
    with tempfile.TemporaryFile() as f:
        if call("dump", sid, "question-authoring.copy", stdout=f) != 0:
            raise BoundaryError("UNAVAILABLE", "스냅숏을 내려받지 못했다")
        f.seek(0)
        data = f.read()
    blocks = _BLOCK.findall(data)
    if [t.decode() for t, _ in blocks] != list(TABLES):
        raise BoundaryError("INTEGRITY_ERROR", "스냅숏의 표 목록이 이 영역의 표와 다르다")      # 모르는 표를 SQL 에 넣지 않는다
    p = psycopg.conninfo.conninfo_to_dict(pg)
    try:
        with psycopg.connect(autocommit=False, **p) as c:
            for t in TABLES:
                if c.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone():
                    raise BoundaryError("FAILED_PRECONDITION", "데이터베이스가 비어 있지 않다. 복구는 빈 데이터베이스에만 한다")
            for t, body in blocks:
                with c.cursor().copy(f"COPY {t.decode()} FROM STDIN") as cp:
                    cp.write(body)
            for t in ("reviews", "drafts"):                        # COPY 는 BIGSERIAL 순번을 올리지 않는다: 다음 삽입이 겹치지 않게 맞춘다
                c.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'seq'), COALESCE((SELECT max(seq) FROM {t}), 0) + 1, false)")
    except BoundaryError:
        raise
    except Exception as e:                                        # noqa: BLE001
        raise BoundaryError("UNAVAILABLE", f"복구하지 못했다({type(e).__name__})") from None
    return {"snapshot_id": sid, "bytes": len(data)}


def _snapshot_id(out: bytes):
    """restic backup --json 의 마지막 summary 줄에서 스냅숏 ID. 없으면 null(백업은 성공했으나 ID 를 읽지 못함)."""
    import json
    sid = None
    for line in out.decode("utf-8", "replace").splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if isinstance(m, dict) and m.get("message_type") == "summary" and isinstance(m.get("snapshot_id"), str):
            sid = m["snapshot_id"][:128]
    return sid


def result_line(started: str, finished: str, *, ok: dict | None = None, error: BoundaryError | None = None) -> dict:
    """계약 BackupJobResult(3.4.7). 실패에도 마지막 줄 JSON 을 낸다(R9.2). 비밀·주소·데이터는 넣지 않는다."""
    if error is None:
        return {"status": "ok", "snapshot_id": ok["snapshot_id"], "bytes": ok["bytes"], "started_at": started, "finished_at": finished, "error": None}
    return {"status": "failed", "snapshot_id": None, "bytes": 0, "started_at": started, "finished_at": finished,
            "error": {"code": error.code, "message": error.message, "retryable": bool(error.retryable)}}


def exit_code(error: BoundaryError | None) -> int:
    """0 = ok, 75 = 재시도 가능(저장소에 닿지 못함, EX_TEMPFAIL), 1 = 그 밖의 실패(BackupJobResult)."""
    if error is None:
        return 0
    return 75 if error.code == "UNAVAILABLE" else 1
