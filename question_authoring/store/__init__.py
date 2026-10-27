from .base import AuthoringStore, QuestionReaderView, bank_hash
from .memory import MemoryQuestionStore
from .sqlite import SqliteQuestionStore


def PostgresQuestionStore(*a, **kw):
    """psycopg는 PostgreSQL을 쓸 때만 불러온다(SQLite·메모리만 쓰는 이웃은 드라이버가 필요 없다)."""
    from .postgres import PostgresQuestionStore as _P
    return _P(*a, **kw)


__all__ = ["AuthoringStore", "QuestionReaderView", "bank_hash", "MemoryQuestionStore", "SqliteQuestionStore",
           "PostgresQuestionStore"]
