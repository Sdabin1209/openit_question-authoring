"""스키마 마이그레이션(yoyo, ADR 0017): 마이그레이션 도입 이전에 만든 DB를 그대로 받아들이고, 모르는 미래 DB와 미적용 읽기 전용은 거부한다.
OLD_*는 도입 이전 코드(커밋 f710c11의 SCHEMA)가 만들던 DDL의 사본이다. 이 시험이 '이미 있는 스키마에서 시작'의 기준이다."""
import sqlite3

import pytest
from boundary_contracts.errors import BoundaryError

from conftest import pg_schema
from helpers import AUTHOR, publish
from question_authoring.service import AuthoringService
from question_authoring.store import SqliteQuestionStore

OLD_SQLITE = """
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE versions(qsid TEXT NOT NULL, version_no INTEGER NOT NULL, content_hash TEXT NOT NULL, bank TEXT NOT NULL,
  source TEXT, PRIMARY KEY(qsid, version_no));
CREATE TABLE reviews(seq INTEGER PRIMARY KEY AUTOINCREMENT, review_id TEXT NOT NULL UNIQUE, qsid TEXT NOT NULL,
  version_no INTEGER NOT NULL, record TEXT NOT NULL);
CREATE INDEX reviews_ver ON reviews(qsid, version_no);
CREATE TABLE changes(qsid TEXT NOT NULL, version_no INTEGER NOT NULL, record TEXT NOT NULL, PRIMARY KEY(qsid, version_no));
CREATE TABLE drafts(draft_id TEXT PRIMARY KEY, qsid TEXT NOT NULL, record TEXT NOT NULL);
CREATE TABLE blobs(source_hash TEXT PRIMARY KEY, filename TEXT NOT NULL, data BLOB NOT NULL{created});
INSERT INTO meta(k, v) VALUES('schema_version', '1');
"""
for _t in ("versions", "reviews", "changes"):
    for _op in ("UPDATE", "DELETE"):
        OLD_SQLITE += f"CREATE TRIGGER {_t}_no_{_op.lower()} BEFORE {_op} ON {_t} BEGIN SELECT RAISE(ABORT, '{_t}는 불변이다'); END;\n"


def old_db(path, with_created_at):
    c = sqlite3.connect(path)
    c.executescript(OLD_SQLITE.format(created=", created_at TEXT" if with_created_at else ""))
    c.execute("INSERT INTO blobs(source_hash, filename, data) VALUES('sha256:" + "a" * 64 + "', 'f.txt', x'616263')")
    c.commit(); c.close()


def applied(path):
    c = sqlite3.connect(path)
    try:
        return [r[0] for r in c.execute("SELECT migration_id FROM qa_migration ORDER BY applied_at_utc, migration_id")]
    finally:
        c.close()


@pytest.mark.parametrize("with_created_at", [False, True])
def test_sqlite_adopts_a_database_made_before_migrations(tmp_path, validators, with_created_at):
    path = tmp_path / "old.sqlite3"
    old_db(path, with_created_at)
    s = SqliteQuestionStore(path)
    assert s.get_source("sha256:" + "a" * 64) is not None          # 기존 자료가 그대로 있다
    cols = [r[1] for r in s._db.execute("PRAGMA table_info(blobs)").fetchall()]
    assert "created_at" in cols
    ref = publish(AuthoringService(s, validators))                    # 새 쓰기도 된다
    with pytest.raises(sqlite3.Error):
        s._db.execute("UPDATE versions SET content_hash='x'")         # 불변 트리거가 살아 있다
    s.close()
    assert applied(path) == ["0001.initial", "0002.blobs-created-at"]
    s = SqliteQuestionStore(path)                                     # 다시 열어도 멱등
    assert s.latest(ref["id"]) == ref
    s.close()
    assert applied(path) == ["0001.initial", "0002.blobs-created-at"]


def test_sqlite_fresh_database_is_migrated(tmp_path):
    path = tmp_path / "new.sqlite3"
    SqliteQuestionStore(path).close()
    assert applied(path) == ["0001.initial", "0002.blobs-created-at"]


def test_sqlite_refuses_a_database_with_unknown_migrations(tmp_path):
    path = tmp_path / "future.sqlite3"
    SqliteQuestionStore(path).close()
    c = sqlite3.connect(path)
    c.execute("INSERT INTO qa_migration(migration_hash, migration_id, applied_at_utc) VALUES('ff', '9999.future', '2030-01-01 00:00:00')")
    c.commit(); c.close()
    with pytest.raises(BoundaryError) as e:
        SqliteQuestionStore(path)
    assert e.value.code == "UNAVAILABLE"


def test_sqlite_read_only_refuses_unmigrated_database_and_does_not_change_it(tmp_path):
    path = tmp_path / "old.sqlite3"
    old_db(path, True)
    with pytest.raises(BoundaryError) as e:
        SqliteQuestionStore(path, read_only=True)
    assert e.value.code == "UNAVAILABLE"
    c = sqlite3.connect(path)
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    c.close()
    assert not any("yoyo" in n or n == "qa_migration" for n in names)


def test_postgres_adopts_a_database_made_before_migrations(validators):
    import psycopg
    from question_authoring.store import PostgresQuestionStore
    with pg_schema() as (url, schema):
        with psycopg.connect(url, autocommit=True) as c:
            c.execute(f"SET search_path TO {schema}")
            c.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT NOT NULL)")
            c.execute("CREATE TABLE versions(qsid TEXT NOT NULL, version_no INTEGER NOT NULL, content_hash TEXT NOT NULL, bank TEXT NOT NULL, source TEXT, PRIMARY KEY(qsid, version_no))")
            c.execute("CREATE TABLE reviews(seq BIGSERIAL PRIMARY KEY, review_id TEXT NOT NULL UNIQUE, qsid TEXT NOT NULL, version_no INTEGER NOT NULL, record TEXT NOT NULL)")
            c.execute("CREATE TABLE changes(qsid TEXT NOT NULL, version_no INTEGER NOT NULL, record TEXT NOT NULL, PRIMARY KEY(qsid, version_no))")
            c.execute("CREATE TABLE drafts(seq BIGSERIAL, draft_id TEXT PRIMARY KEY, qsid TEXT NOT NULL, record TEXT NOT NULL)")
            c.execute("CREATE TABLE blobs(source_hash TEXT PRIMARY KEY, filename TEXT NOT NULL, data BYTEA NOT NULL)")
            c.execute("INSERT INTO meta(k, v) VALUES('schema_version', '1')")
            c.execute("INSERT INTO blobs(source_hash, filename, data) VALUES('sha256:" + "a" * 64 + "', 'f.txt', '\\x616263')")
        s = PostgresQuestionStore(url, schema=schema)
        assert s.get_source("sha256:" + "a" * 64) is not None
        ref = publish(AuthoringService(s, validators))
        with pytest.raises(psycopg.Error):
            s._db.execute("UPDATE versions SET content_hash='x'")     # 불변 트리거가 생겼다
        s.close()
        s = PostgresQuestionStore(url, schema=schema)                  # 멱등
        assert s.latest(ref["id"]) == ref
        ids = [r[0] for r in s._db.execute("SELECT migration_id FROM qa_migration ORDER BY migration_id").fetchall()]
        assert ids == ["0001.initial", "0002.blobs-created-at"]
        s.close()


def test_postgres_read_only_refuses_unmigrated_database():
    from question_authoring.store import PostgresQuestionStore
    with pg_schema() as (url, schema):
        with pytest.raises(BoundaryError) as e:
            PostgresQuestionStore(url, schema=schema, read_only=True)
        assert e.value.code == "UNAVAILABLE"
