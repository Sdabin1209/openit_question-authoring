import sqlite3

import pytest
from boundary_contracts.errors import BoundaryError

from question_authoring.wiring.service import AuthoringService
from question_authoring.wiring.stores import SqliteQuestionStore
from helpers import AUTHOR, publish


def test_immutable_triggers(tmp_path, validators):
    s = SqliteQuestionStore(tmp_path / "a.sqlite3")
    svc = AuthoringService(s)
    publish(svc)
    publish(svc, edits=[{"op": "set", "path": "/questions/0/name", "value": "체온 측정"}])
    for sql in ("UPDATE versions SET bank='{}'", "DELETE FROM versions", "UPDATE reviews SET record='{}'",
                "DELETE FROM reviews", "UPDATE changes SET record='{}'", "DELETE FROM changes"):
        with pytest.raises(sqlite3.Error):
            s._db.execute(sql)
    s.close()


def test_persistence_and_read_only(tmp_path, validators):
    path = tmp_path / "b.sqlite3"
    s = SqliteQuestionStore(path)
    ref = publish(AuthoringService(s))
    s.close()
    ro = SqliteQuestionStore(path, read_only=True)
    assert ro.reader().get_question_bank(ref)["version"] == 1
    with pytest.raises(BoundaryError) as e:
        ro.add_review({"question_set": ref, "review_id": "rv-z"})
    assert e.value.code == "PERMISSION_DENIED"
    with pytest.raises(sqlite3.Error):
        ro._db.execute("DELETE FROM versions")
    ro.close()
    s2 = SqliteQuestionStore(path)
    assert s2.latest("qs-main") == ref
    s2.close()


def test_reader_view_has_no_write_methods(store):
    r = store.reader()
    assert not any(hasattr(r, n) for n in ("commit_version", "add_review", "save_draft", "put_source"))
