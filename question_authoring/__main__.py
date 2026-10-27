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
"""
import os
import sys

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
            probe_timeout=_seconds("QA_READYZ_DB_TIMEOUT_SECONDS", 2.0))
    return SqliteQuestionStore(os.environ.get("QA_DB_PATH", "/data/questions.sqlite3"))


def build() -> tuple[Api, AuthoringStore]:
    env = os.environ
    tokens = {}
    for item in filter(None, (read_secret("QA_AUTHOR_TOKENS") or "").split(",")):
        tok, aid, role = item.split(":")
        tokens[tok] = Actor(aid, role)
    store = open_store()
    v = Validators()
    read_tokens = {}
    for item in filter(None, (read_secret("QA_READ_TOKENS") or "").split(",")):
        role, tok = item.split("=", 1)
        read_tokens[tok] = role
    svc = AuthoringService(store, v, author_roles={a.role for a in tokens.values()} or {"question-author"})
    signals = None
    if env.get("QA_FAILURES_URL"):
        from boundary_contracts.clients import HttpFailureStore
        signals = HttpFailureStore(env["QA_FAILURES_URL"], read_secret(env.get("QA_FAILURES_TOKEN_ENV", "QA_FAILURES_TOKEN")) or "")
    return Api(svc, tokens, read_tokens=read_tokens, signals=signals, ready=store.is_ready, db_kind=getattr(store, "kind", None)), store


def main() -> int:
    api, _ = build()
    srv = make_server(api, os.environ.get("QA_HOST", "0.0.0.0"), int(os.environ.get("QA_PORT", "8080")))
    print("question-authoring 시작", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
