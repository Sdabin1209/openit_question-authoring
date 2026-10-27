"""서비스 진입점. 환경 변수를 읽는 곳은 여기뿐이다.

QA_DATABASE_URL       PostgreSQL 접속 문자열(운영). 비밀 값: QA_DATABASE_URL_FILE 우선. 있으면 PostgreSQL을 쓴다
QA_DB_PATH            QA_DATABASE_URL이 없을 때 쓰는 SQLite 파일 경로(개발·시험, 기본 /data/questions.sqlite3)
비밀(QA_AUTHOR_TOKENS, QA_READ_TOKENS, QA_FAILURES_TOKEN)은 `boundary_contracts.read_secret`으로 읽는다:
NAME_FILE(파일 경로)이 우선이고, 없을 때만 NAME 환경 변수(개발용)를 쓴다. 운영에서는 NAME_FILE을 쓴다.
QA_AUTHOR_TOKENS      '토큰:actor-id:역할' 쉼표 목록. 비밀 값이므로 이미지에 넣지 않는다
QA_READ_TOKENS        '역할=토큰' 쉼표 목록. I1 읽기 전용 토큰(저작 경로는 403). 비밀 값
QA_FAILURES_URL      ops-feedback(I7) 기본 URL. 비우면 개선 신호는 DISABLED로 응답
QA_FAILURES_TOKEN_ENV I7 읽기 토큰의 비밀 이름(기본 QA_FAILURES_TOKEN; QA_FAILURES_TOKEN_FILE로도 준다). 값은 이미지에 넣지 않는다
QA_HOST, QA_PORT      기본 0.0.0.0, 8080
QA_DB_CONNECT_TIMEOUT_SECONDS    PostgreSQL 접속 상한(초, 기본 3)
QA_DB_STATEMENT_TIMEOUT_SECONDS  PostgreSQL 문장 하나의 상한(초, 기본 3). 호출 하나는 이 값 + 1초 안에 끝난다
QA_READYZ_DB_TIMEOUT_SECONDS     /readyz의 DB 확인 상한(초, 기본 2)
QA_DB_LARGE_STATEMENT_TIMEOUT_SECONDS  큰 자료(원본 자료, 초안, 문항 세트 버전) 쓰기의 문장 상한(초, 기본 30)
QA_SHUTDOWN_GRACE_SECONDS       SIGTERM 뒤 진행 중 요청을 기다리는 최대 시간(기본 8초)
QA_REQUEST_TIMEOUT_SECONDS       HTTP 요청 소켓의 무응답 상한(초, 기본 30). 업로드가 이 시간 동안 멈추면 끊는다
QA_DEV_MODE                      1이면 개발·시험 구성을 허용한다(SQLite, 토큰 없이 기동). 없으면 운영 구성이어야 시작한다:
                                 QA_DATABASE_URL(_FILE)과 토큰(QA_AUTHOR_TOKENS 또는 QA_GATEWAY_TOKENS)이 없으면 시작을 거부한다
QA_REQUIRE_TWO_PERSON            기본 true. 저자 != 검수자(4-eyes, D88 U9). false로 두면 이전 동작이고 /readyz에 경고가 뜬다
QA_GATEWAY_TOKENS                비밀. gateway가 쓰는 서비스 토큰 쉼표 목록. 이 토큰의 요청은 X-Openit-User 헤더의 사용자를 쓴다
QA_USER_ROLES                    '사용자ID=역할' 쉼표 목록(역할: question-author, clinical-reviewer). gateway 경유 사용자의 역할표
QA_MAX_CONCURRENT_REQUESTS       동시 처리 요청 상한(기본 32). 넘으면 503 + Retry-After
QA_DRAFT_RETENTION_DAYS          처리되지 않은(OPEN, DISCARDED) 초안의 보존 일수(기본 30, 양의 정수만). `python -m question_authoring purge`가 쓴다
QA_SOURCE_RETENTION_DAYS         어떤 버전·초안도 참조하지 않는 원본 자료의 보존 일수(기본 30, 양의 정수만)
QA_FAILURES_TIMEOUT_SECONDS      I7(ops-feedback) 호출 하나의 읽기 상한(초, 기본 5)
QA_SIGNALS_DEADLINE_SECONDS      GET /v1/signals 한 번의 총 마감(초, 기본 20)
"""
import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

from boundary_contracts import read_secret

from .http_api import Api, make_server
from .service import Actor, AuthoringService
from .store import AuthoringStore, PostgresQuestionStore, SqliteQuestionStore
from .validation import Validators


def _seconds(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        v = float(raw)
    except ValueError:
        raise SystemExit(f"{name}은 초 단위 숫자여야 한다")
    if not 0 < v <= 600:
        raise SystemExit(f"{name}은 0보다 크고 600 이하여야 한다")
    return v


def open_store() -> AuthoringStore:
    url = read_secret("QA_DATABASE_URL")
    if url:
        return PostgresQuestionStore(
            url, connect_timeout=_seconds("QA_DB_CONNECT_TIMEOUT_SECONDS", 3.0),
            statement_timeout=_seconds("QA_DB_STATEMENT_TIMEOUT_SECONDS", 3.0),
            probe_timeout=_seconds("QA_READYZ_DB_TIMEOUT_SECONDS", 2.0),
            large_statement_timeout=_seconds("QA_DB_LARGE_STATEMENT_TIMEOUT_SECONDS", 30.0))
    return SqliteQuestionStore(os.environ.get("QA_DB_PATH", "/data/questions.sqlite3"))


ROLES = ("question-author", "clinical-reviewer")


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    v = raw.strip().lower()
    if v in ("1", "true"):
        return True
    if v in ("0", "false"):
        return False
    raise SystemExit(f"{name}은 true 또는 false여야 한다")


def _days(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    if not raw.strip().isdigit() or int(raw) < 1:
        raise SystemExit(f"{name}은 1 이상의 정수(일)여야 한다. 보존 기간 없음(무기한)은 허용하지 않는다")
    return int(raw)


def _require_production_config(*, need_tokens: bool) -> None:
    """운영 구성 검사(전수 점검 P6). QA_DEV_MODE=1이 아니면 DB URL(과 서비스 기동이면 토큰)이 있어야 한다.
    purge도 같은 검사를 거친다: 개발 플래그 없이 빈 임시 SQLite를 열어 "0건 삭제"로 성공하는 일이 없게 한다."""
    if _bool("QA_DEV_MODE", False):
        return
    if not read_secret("QA_DATABASE_URL"):
        raise SystemExit("QA_DATABASE_URL(_FILE)이 없다. 개발·시험이면 QA_DEV_MODE=1을 준다")
    if need_tokens and not (read_secret("QA_AUTHOR_TOKENS") or read_secret("QA_GATEWAY_TOKENS")):
        raise SystemExit("저자 토큰이 없다(QA_AUTHOR_TOKENS 또는 QA_GATEWAY_TOKENS). 개발·시험이면 QA_DEV_MODE=1을 준다")


def build() -> tuple[Api, AuthoringStore]:
    env = os.environ
    dev = _bool("QA_DEV_MODE", False)
    _require_production_config(need_tokens=True)
    tokens = {}
    for item in filter(None, (read_secret("QA_AUTHOR_TOKENS") or "").split(",")):
        tok, aid, role = item.split(":")
        if role not in ROLES:
            raise SystemExit(f"QA_AUTHOR_TOKENS의 역할은 {', '.join(ROLES)} 중 하나여야 한다")
        tokens[tok] = Actor(aid, role)
    gateway_tokens = {t for t in (read_secret("QA_GATEWAY_TOKENS") or "").split(",") if t}
    user_roles = {}
    for item in filter(None, (env.get("QA_USER_ROLES") or "").split(",")):
        uid, _, role = item.partition("=")
        if role not in ROLES:
            raise SystemExit(f"QA_USER_ROLES의 역할은 {', '.join(ROLES)} 중 하나여야 한다")
        user_roles[uid.strip()] = role
    if not dev:
        if gateway_tokens and not user_roles:
            raise SystemExit("QA_GATEWAY_TOKENS가 있는데 QA_USER_ROLES가 비어 있다")
    store = open_store()
    v = Validators()
    read_tokens = {}
    for item in filter(None, (read_secret("QA_READ_TOKENS") or "").split(",")):
        role, tok = item.split("=", 1)
        read_tokens[tok] = role
    two = _bool("QA_REQUIRE_TWO_PERSON", True)
    svc = AuthoringService(store, v, author_roles={"question-author"}, require_two_person=two)
    warnings = ([] if two else ["two_person_disabled"])
    signals = None
    if env.get("QA_FAILURES_URL"):
        from boundary_contracts.clients import HttpFailureStore
        signals = HttpFailureStore(env["QA_FAILURES_URL"], read_secret(env.get("QA_FAILURES_TOKEN_ENV", "QA_FAILURES_TOKEN")) or "",
                                   timeout=_seconds("QA_FAILURES_TIMEOUT_SECONDS", 5.0))
    return Api(svc, tokens, read_tokens=read_tokens, signals=signals, ready=store.is_ready, db_kind=getattr(store, "kind", None),
               signals_deadline=_seconds("QA_SIGNALS_DEADLINE_SECONDS", 20.0), gateway_tokens=gateway_tokens,
               user_roles=user_roles, warnings=warnings), store


def purge_once(store: AuthoringStore, now: datetime | None = None) -> dict:
    """보존 기간이 지난 것을 지운다(멱등, 여러 번 돌려도 안전). 스케줄은 deploy가 정한다(D39)."""
    now = now or datetime.now(timezone.utc)
    ts = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")   # noqa: E731
    return store.purge(now=ts(now), draft_before=ts(now - timedelta(days=_days("QA_DRAFT_RETENTION_DAYS", 30))),
                       source_before=ts(now - timedelta(days=_days("QA_SOURCE_RETENTION_DAYS", 30))))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["purge"]:
        _require_production_config(need_tokens=False)   # 정리 잡에는 저자 토큰이 필요 없어 DB URL과 개발 플래그만 본다
        store = open_store()
        print(purge_once(store), flush=True)
        store.close()
        return 0
    api, _ = build()
    srv = make_server(api, os.environ.get("QA_HOST", "0.0.0.0"), int(os.environ.get("QA_PORT", "8080")),
                      request_timeout=_seconds("QA_REQUEST_TIMEOUT_SECONDS", 30.0),
                      max_concurrent=int(_seconds("QA_MAX_CONCURRENT_REQUESTS", 32)))

    def stop(signum, frame):   # PID 1의 Python은 SIGTERM을 기본으로 무시한다. 새 연결을 받지 않고, 아래에서 진행 중 요청을 유예 시간까지 기다린다
        print("종료 신호를 받았다. 새 요청을 받지 않고 진행 중 요청을 기다린다", flush=True)
        threading.Thread(target=srv.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print("question-authoring 시작", flush=True)
    srv.serve_forever()
    # 진행 중 요청을 유예 시간까지 기다린다. 기본 8초: docker stop의 기본 대기(10초) 뒤 SIGKILL보다 짧게 끝내려는 값이다(근거: Docker 문서, 측정 안 함)
    grace = _seconds("QA_SHUTDOWN_GRACE_SECONDS", 8.0)
    end = time.monotonic() + grace
    while srv.inflight() > 0 and time.monotonic() < end:
        time.sleep(0.05)
    left = srv.inflight()
    srv.server_close()
    if left:
        print(f"유예 시간 {grace}초가 지나 처리 중인 요청 {left}개를 두고 종료한다", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
