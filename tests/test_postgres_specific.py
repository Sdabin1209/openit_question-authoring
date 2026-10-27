"""PostgreSQL 구현에만 해당하는 시험. TEST_DATABASE_URL이 없으면 SKIP."""
import psycopg
import pytest
from boundary_contracts.errors import BoundaryError

from conftest import pg_schema
from question_authoring.service import AuthoringService
from question_authoring.store import PostgresQuestionStore
from helpers import publish


def test_immutable_triggers(validators):
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema)
        svc = AuthoringService(s, validators)
        publish(svc)
        publish(svc, edits=[{"op": "set", "path": "/questions/0/name", "value": "체온 측정"}])
        for sql in ("UPDATE versions SET bank='{}'", "DELETE FROM versions", "UPDATE reviews SET record='{}'",
                    "DELETE FROM reviews", "UPDATE changes SET record='{}'", "DELETE FROM changes"):
            with pytest.raises(psycopg.Error):
                s._db.execute(sql)
        s.close()


def test_schema_creation_is_idempotent_and_persistent(validators):
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema)
        ref = publish(AuthoringService(s, validators))
        s.close()
        s2 = PostgresQuestionStore(url, schema=schema)   # 두 번째 시작: 스키마 재생성이 데이터를 건드리지 않는다
        s3 = PostgresQuestionStore(url, schema=schema)
        assert s2.latest("qs-main") == ref and s3.latest("qs-main") == ref
        s2.close(); s3.close()


def test_read_only_connection(validators):
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema)
        ref = publish(AuthoringService(s, validators))
        ro = PostgresQuestionStore(url, schema=schema, read_only=True)
        assert ro.reader().get_question_bank(ref)["version"] == 1
        with pytest.raises(BoundaryError) as e:
            ro.add_review({"question_set": ref, "review_id": "rv-z"})
        assert e.value.code == "PERMISSION_DENIED"
        with pytest.raises(psycopg.Error):
            ro._db.execute("INSERT INTO meta(k, v) VALUES('x','y')")
        ro.close(); s.close()


def test_reconnects_after_connection_loss(validators):
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema)
        ref = publish(AuthoringService(s, validators))
        s._db.close()
        assert s.latest("qs-main") == ref   # 닫힌 연결은 다음 호출에서 다시 연다
        s.close()


def test_failure_messages_have_no_url_or_password():
    pw = "SECRET-dbpw-5e1f"
    url = f"postgresql://qa:{pw}@127.0.0.1:1/nodb"       # 닫힌 포트: 서버 없이도 돈다
    with pytest.raises(BoundaryError) as e:
        PostgresQuestionStore(url)
    assert e.value.code == "UNAVAILABLE"
    for text in (str(e.value), repr(e.value), repr(e.value.to_record())):
        assert pw not in text and "127.0.0.1" not in text
    assert e.value.__context__ is None
    with pytest.raises(BoundaryError) as e2:
        PostgresQuestionStore(f"postgres://qa:{pw}@[bad/db")  # 형식 오류
    assert pw not in str(e2.value) and e2.value.__context__ is None
