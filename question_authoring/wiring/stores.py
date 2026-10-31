"""저장소 연결: 백엔드 컴포넌트(메모리·SQLite·PostgreSQL)를 저장소 컴포넌트(QuestionStore)에 주입한다. 실제 시계·타이머도 여기서 넣는다(ADR 0024)."""
from __future__ import annotations

import threading

from ..components.question_store import QuestionStore
from ..components import store_memory, store_sqlite
from . import schemas
from .ids import now_default, ts


def now_ts() -> str:
    """저장 시각 문자열(UTC, 초 단위)."""
    return ts(now_default())


def MemoryQuestionStore(**kw) -> QuestionStore:   # noqa: N802  이전 클래스 이름 유지(이웃 시험이 같은 이름으로 만든다)
    kw.setdefault("now_ts", now_ts)
    return QuestionStore(store_memory.MemoryQuestionStore(**kw), schemas.check)


def SqliteQuestionStore(*a, **kw) -> QuestionStore:   # noqa: N802
    kw.setdefault("now_ts", now_ts)
    return QuestionStore(store_sqlite.SqliteQuestionStore(*a, **kw), schemas.check)


def PostgresQuestionStore(*a, **kw) -> QuestionStore:   # noqa: N802
    """psycopg는 PostgreSQL을 쓸 때만 불러온다(SQLite·메모리만 쓰는 이웃은 드라이버가 필요 없다)."""
    from ..components import store_postgres
    kw.setdefault("now_ts", now_ts)
    kw.setdefault("timer", _real_timer)
    kw.setdefault("wait", _real_wait)
    return QuestionStore(store_postgres.PostgresQuestionStore(*a, **kw), schemas.check)


def _real_timer(seconds, fn):
    return threading.Timer(seconds, fn)


def _real_wait(event, seconds):
    return event.wait(seconds)
