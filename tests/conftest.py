import itertools
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


@pytest.fixture(params=["memory", "sqlite", "sqlite-file"])
def store(request, tmp_path):
    if request.param == "memory":
        s = MemoryQuestionStore()
    elif request.param == "sqlite":
        s = SqliteQuestionStore()
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
