"""저장소 연결: 백엔드 컴포넌트(메모리·SQLite·PostgreSQL)를 저장소 컴포넌트(QuestionStore)에 주입한다."""
from __future__ import annotations

from ..components.question_store import QuestionStore
from ..components import store_memory, store_sqlite
from . import schemas


def MemoryQuestionStore() -> QuestionStore:   # noqa: N802  이전 클래스 이름 유지(이웃 시험이 같은 이름으로 만든다)
    return QuestionStore(store_memory.MemoryQuestionStore(), schemas.check)


def SqliteQuestionStore(*a, **kw) -> QuestionStore:   # noqa: N802
    return QuestionStore(store_sqlite.SqliteQuestionStore(*a, **kw), schemas.check)


def PostgresQuestionStore(*a, **kw) -> QuestionStore:   # noqa: N802
    """psycopg는 PostgreSQL을 쓸 때만 불러온다(SQLite·메모리만 쓰는 이웃은 드라이버가 필요 없다)."""
    from ..components import store_postgres
    return QuestionStore(store_postgres.PostgresQuestionStore(*a, **kw), schemas.check)
