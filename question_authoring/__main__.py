"""서비스 진입점. 환경 변수를 읽는 곳은 여기뿐이다.

QA_DB_PATH            SQLite 파일 경로(기본 /data/questions.sqlite3)
QA_AUTHOR_TOKENS      '토큰:actor-id:역할' 쉼표 목록. 비밀 값이므로 이미지에 넣지 않는다
QA_BOUNDARY_SCHEMAS   boundary-contracts schemas 디렉터리
QA_SURVEY_SCHEMA      문진 스키마 디렉터리(CONTRACT_VERSION과 schemas/가 있는 곳)
QA_HOST, QA_PORT      기본 0.0.0.0, 8080
"""
import os
import sys
from pathlib import Path

from .http_api import Api, make_server
from .service import Actor, AuthoringService
from .store import SqliteQuestionStore
from .validation import Validators


def build() -> tuple[Api, SqliteQuestionStore]:
    env = os.environ
    tokens = {}
    for item in filter(None, (env.get("QA_AUTHOR_TOKENS") or "").split(",")):
        tok, aid, role = item.split(":")
        tokens[tok] = Actor(aid, role)
    store = SqliteQuestionStore(env.get("QA_DB_PATH", "/data/questions.sqlite3"))
    v = Validators(Path(env["QA_BOUNDARY_SCHEMAS"]) if env.get("QA_BOUNDARY_SCHEMAS") else None,
                   Path(env["QA_SURVEY_SCHEMA"]) if env.get("QA_SURVEY_SCHEMA") else None)
    svc = AuthoringService(store, v, author_roles={a.role for a in tokens.values()} or {"question-author"})
    return Api(svc, tokens, ready=store.is_ready), store


def main() -> int:
    api, _ = build()
    srv = make_server(api, os.environ.get("QA_HOST", "0.0.0.0"), int(os.environ.get("QA_PORT", "8080")))
    print("question-authoring 시작", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
