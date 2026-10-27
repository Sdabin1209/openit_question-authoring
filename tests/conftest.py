import contextlib
import itertools
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from question_authoring.service import Actor, AuthoringService
from question_authoring.store import MemoryQuestionStore, SqliteQuestionStore
from question_authoring.validation import Validators

ROOT = Path(__file__).resolve().parent.parent
AUTHOR = Actor("dr-kim", "question-author")


@pytest.fixture(scope="session")
def validators():
    return Validators()


@contextlib.contextmanager
def pg_schema():
    """TEST_DATABASE_URL이 없으면 SKIP(PASS로 세지 않는다). 있으면 시험마다 빈 스키마를 만들고 끝나면 지운다."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 없음: PostgreSQL 시험 건너뜀")
    import psycopg
    schema = "t_" + uuid.uuid4().hex[:12]
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {schema}")
        try:
            yield url, schema
        finally:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture(params=["memory", "sqlite", "sqlite-file", "postgres"])
def store(request, tmp_path):
    if request.param == "memory":
        s = MemoryQuestionStore()
    elif request.param == "sqlite":
        s = SqliteQuestionStore()
    elif request.param == "postgres":
        with pg_schema() as (url, schema):
            from question_authoring.store import PostgresQuestionStore
            s = PostgresQuestionStore(url, schema=schema)
            yield s
            s.close()
        return
    else:
        s = SqliteQuestionStore(tmp_path / "q.sqlite3")
    yield s
    s.close()


class Tick:
    def __init__(self):
        self.t = datetime(2026, 10, 15, 9, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


@pytest.fixture
def svc(store, validators):
    c = itertools.count(1)
    return AuthoringService(store, validators, clock=Tick(), new_id=lambda p: f"{p}-{next(c):04d}")


def example_text() -> bytes:
    return (ROOT / "examples" / "question_form.txt").read_bytes()
