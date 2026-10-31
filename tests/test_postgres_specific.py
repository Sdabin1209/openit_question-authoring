"""PostgreSQL 구현에만 해당하는 시험. TEST_DATABASE_URL이 없으면 SKIP."""
import json
import psycopg
import pytest
from boundary_contracts.errors import BoundaryError

from conftest import pg_schema
from question_authoring.wiring.service import AuthoringService
from question_authoring.wiring.stores import PostgresQuestionStore
from helpers import publish, AUTHOR as AUTHOR_FOR_PG


def test_immutable_triggers(validators):
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema)
        svc = AuthoringService(s)
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
        ref = publish(AuthoringService(s))
        s.close()
        s2 = PostgresQuestionStore(url, schema=schema)   # 두 번째 시작: 스키마 재생성이 데이터를 건드리지 않는다
        s3 = PostgresQuestionStore(url, schema=schema)
        assert s2.latest("qs-main") == ref and s3.latest("qs-main") == ref
        s2.close(); s3.close()


def test_read_only_connection(validators):
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema)
        ref = publish(AuthoringService(s))
        ro = PostgresQuestionStore(url, schema=schema, read_only=True)
        assert ro.reader().get_question_bank(ref)["version"] == 1
        with pytest.raises(BoundaryError) as e:
            ro.add_review({"question_set": ref, "review_id": "rv-z"})
        assert e.value.code == "PERMISSION_DENIED"
        with pytest.raises(psycopg.Error):
            ro._db.execute("INSERT INTO drafts(draft_id, qsid, record) VALUES('x','y','z')")
        ro.close(); s.close()


def test_reconnects_after_connection_loss(validators):
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema)
        ref = publish(AuthoringService(s))
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


def _kill_backend(url, s):
    """다른 연결에서 이 저장소의 서버 프로세스를 강제 종료한다(DB 서버 재시작과 같은 끊김)."""
    pid = s._db.info.backend_pid
    with psycopg.connect(url, autocommit=True) as admin:
        assert admin.execute("SELECT pg_terminate_backend(%s)", (pid,)).fetchone()[0]


def test_recovers_after_backend_terminated_read_and_write(validators):
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema)
        svc = AuthoringService(s)
        ref = publish(svc)
        _kill_backend(url, s)
        assert s.latest("qs-main") == ref                       # 읽기: 재연결 후 한 번 재시도
        _kill_backend(url, s)
        ref2 = publish(svc, edits=[{"op": "set", "path": "/questions/0/name", "value": "체온 측정"}])  # 쓰기 여러 번
        assert ref2["version"] == "2"
        assert [v for v, _ in s._version_rows("qs-main")] == [1, 2]   # 중복 없이 정확히 한 번
        s.close()


def test_write_retry_after_lost_commit_ack_is_idempotent(validators):
    """커밋은 됐는데 응답을 잃은 경우를 흉내 낸다: 같은 commit_version을 다시 불러도 조용히 성공하고 중복이 없다."""
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema)
        svc = AuthoringService(s, require_two_person=False)
        ref = publish(svc)
        bank = s.get_question_bank(ref)
        rv = s.get_reviews(ref)[0]
        s.commit_version(bank, ref, rv, None, None)
        assert len(s.get_reviews(ref)) == 1 and len(s._version_rows("qs-main")) == 1
        s.close()


def test_unrecoverable_is_unavailable_without_secret(validators):
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema)
        s._url = "postgresql://qa:SECRET-dbpw-5e1f@127.0.0.1:1/q"   # 서버가 사라진 상황
        _kill_backend(url, s)
        with pytest.raises(BoundaryError) as e:
            s.latest("qs-main")
        assert e.value.code == "UNAVAILABLE" and "SECRET-dbpw-5e1f" not in repr(e.value) + str(e.value.to_record())
        assert e.value.__context__ is None
        assert s.is_ready() is False
        s.close()


def test_non_connection_errors_are_not_retried(validators):
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema)
        publish(AuthoringService(s))
        n = []
        orig = s._conn
        def counting():
            n.append(1); return orig()
        s._conn = counting
        with pytest.raises(BoundaryError) as e:
            s._q("SELECT * FROM no_such_table")
        assert e.value.code == "UNAVAILABLE" and len(n) == 1
        s.close()


# ---- DB 대기 상한 (FAULT_MATRIX P0-3 / E1) ----
# U422: PostgreSQL이 statement_timeout에 문장을 자르는가, psycopg가 접속 시한에 포기하는가, docker pause로 얼린 서버가 어떻게 보이는가는
# 라이브러리·서버의 동작이라 영역 시험이 아니다(smoke·E2E의 몫). 영역의 책임은 (1) 서버·DB·클라이언트에 올바른 값을 넘기는가
# (2) 시간 초과·연결 실패 예외가 났을 때 우리 결과(UNAVAILABLE, 재시도 여부, 비밀 비노출)가 맞는가이다. 아래 시험은 시간을 재지 않는다.
import threading

from helpers import HANG_GUARD_SECONDS, wait_until
from question_authoring.components import store_postgres as sp


class FakeConn:
    """psycopg 연결 대역. behavior(sql)이 예외를 던지거나 값을 돌려준다. 실제 소켓·시계를 쓰지 않는다."""
    closed = False
    broken = False

    def __init__(self, behavior):
        self.behavior, self.sql, self.dead = behavior, [], threading.Event()

    def fileno(self):
        return -1

    def kill(self):
        self.dead.set()

    def transaction(self):
        import contextlib
        return contextlib.nullcontext()

    def execute(self, sql, args=()):
        self.sql.append(sql)
        return self.behavior(self, sql)

    def fetchall(self):
        return []

    def close(self):
        self.closed = True


def test_timeout_settings_are_validated():
    for bad in (0, -1, 601, "3", None):
        with pytest.raises(ValueError):
            PostgresQuestionStore("postgresql://u@h/d", connect_timeout=bad)
    with pytest.raises(ValueError):
        PostgresQuestionStore("postgresql://u@h/d", probe_timeout=0)


def test_server_side_statement_timeout_is_set(validators):
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema, statement_timeout=2)
        assert s._q("SHOW statement_timeout")[0][0] == "2s"
        s.close()


def test_limits_are_passed_to_the_driver(validators):
    """(1) 넘기는 값: 접속 인자(접속 시한, 서버 쪽 문장 상한)와 /readyz 확인 연결의 인자."""
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema, connect_timeout=2, statement_timeout=3, probe_timeout=4, large_statement_timeout=9)
        p = s._params(s.connect_timeout, s.statement_timeout)
        assert p["connect_timeout"] == 2 and "-c statement_timeout=3000" in p["options"]
        seen = {}

        def fake_connect(**kw):
            seen.update(kw)
            raise sp.psycopg.OperationalError("연결 대역")
        orig = sp.psycopg.connect
        sp.psycopg.connect = fake_connect
        try:
            assert s.is_ready() is False                                      # 연결이 안 되면 준비되지 않은 것이다
        finally:
            sp.psycopg.connect = orig
        assert seen["connect_timeout"] == 4 and "-c statement_timeout=4000" in seen["options"]   # 확인 연결은 probe_timeout을 쓴다
        s.close()


def test_large_write_uses_the_large_statement_limit_and_plain_write_does_not(validators):
    """(1) 큰 자료 경로만 문장 상한이 large_statement_timeout으로 바뀐다. 트리거가 삽입 순간의 상한 값을 적는다(시간이 아니라 설정 값을 본다)."""
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema, statement_timeout=0.5, large_statement_timeout=5)
        with psycopg.connect(url, autocommit=True, options=f"-c search_path={schema}") as c:
            c.execute("CREATE TABLE stmt_log(v text)")
            c.execute("CREATE FUNCTION log_stmt() RETURNS trigger AS $$ BEGIN INSERT INTO stmt_log VALUES (current_setting('statement_timeout')); RETURN NEW; END $$ LANGUAGE plpgsql")
            c.execute("CREATE TRIGGER log_stmt BEFORE INSERT ON blobs FOR EACH ROW EXECUTE FUNCTION log_stmt()")
            s.put_source("sha256:" + "a" * 64, "f.txt", b"data")
            assert c.execute("SELECT v FROM stmt_log").fetchall() == [("5s",)]            # 큰 자료 경로: large_statement_timeout
        assert s._write(lambda c2: c2.execute("SELECT current_setting('statement_timeout')").fetchone())[0] == "500ms"   # 일반 쓰기: statement_timeout
        s.close()


@pytest.mark.parametrize("exc", [sp.psycopg.errors.QueryCanceled, sp.psycopg.errors.LockNotAvailable])
def test_server_side_timeout_is_unavailable_and_not_retried(validators, exc):
    """(2) 서버가 문장을 시간 초과로 취소했을 때(문장 상한, 락 대기): UNAVAILABLE이고 같은 호출을 다시 걸지 않는다."""
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema)
        calls = []

        def behavior(conn, sql):
            calls.append(sql)
            raise exc("취소 대역")
        fake = FakeConn(behavior)
        s._conn = lambda: fake
        with pytest.raises(BoundaryError) as e:
            s._q("SELECT 1")
        assert e.value.code == "UNAVAILABLE" and e.value.retryable and len(calls) == 1
        s._conn = PostgresQuestionStore.__dict__.get("_conn", None) and s._conn
        s.close()


class ImmediateTimer:
    """threading.Timer 대역: start()하면 곧바로 시한이 지난 것으로 보고 함수를 부른다. 넘겨받은 초를 기록한다."""
    seconds = []

    def __init__(self, seconds, fn):
        ImmediateTimer.seconds.append(seconds)
        self.fn, self.daemon = fn, False

    def start(self):
        self.fn()

    def cancel(self):
        pass


def test_frozen_server_is_cut_by_the_clients_own_watchdog(validators, monkeypatch):
    """서버가 얼어 응답이 없으면 서버 쪽 상한은 소용없다. 우리 감시 타이머가 시한(문장 상한 + 1초, 큰 자료는 큰 상한 + 1초)에 소켓을 닫아 UNAVAILABLE로 끝낸다.
    시험은 타이머를 주입해 시한이 '지났다'고 하고, 넘긴 시한 값과 결과를 본다. docker pause도 실제 시간도 쓰지 않는다."""
    monkeypatch.setattr(sp.PostgresQuestionStore, "_abort", staticmethod(lambda c: c.kill()))
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema, statement_timeout=2, large_statement_timeout=7)
        s._timer = ImmediateTimer                                           # 만든 뒤에 바꾼다(만드는 동안 실제 연결을 건드리지 않게)
        ImmediateTimer.seconds.clear()

        def hang_until_killed(conn, sql):
            conn.dead.wait(HANG_GUARD_SECONDS)                              # 매달림 감시: 감시 타이머가 닫으면 곧바로 풀린다
            raise sp.psycopg.OperationalError("소켓이 닫혔다")
        fake = FakeConn(hang_until_killed)
        s._conn = lambda: fake
        with pytest.raises(BoundaryError) as e:
            s._q("SELECT 1")
        assert e.value.code == "UNAVAILABLE" and "시간 초과" in e.value.message and len(fake.sql) == 1   # 다시 걸지 않는다
        with pytest.raises(BoundaryError):
            s._write(lambda c: None, large=True)
        assert ImmediateTimer.seconds == [3.0, 8.0]                         # 일반 읽기 2+1, 큰 자료 쓰기 7+1
        s.close()


def test_readyz_gives_up_when_the_probe_does_not_answer(validators):
    """/readyz 확인이 probe_timeout 안에 끝나지 않으면 준비되지 않은 것으로 답한다. 기다림을 주입한 함수로 '시한이 지났다'고 한다."""
    with pg_schema() as (url, schema):
        asked = []
        s = PostgresQuestionStore(url, schema=schema, probe_timeout=4, wait=lambda ev, secs: (asked.append(secs), False)[1])
        assert s.is_ready() is False and asked == [4.0]
        s.close()
        ok = PostgresQuestionStore(url, schema=schema)
        assert ok.is_ready() is True                                         # 같은 DB, 실제 기다림: 준비됨
        ok.close()


def test_readyz_probe_does_not_wait_for_main_connection_or_lock(validators):
    """주 연결이 쓰기(락 대기)에 묶여 있어도 is_ready는 돌아온다(별도 연결). 순서는 조건으로 정한다: 쓰기가 락을 기다리는 것이 보인 뒤에 확인한다."""
    from question_authoring.components.store_postgres import _LOCK_KEY
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema, statement_timeout=HANG_GUARD_SECONDS)   # 쓰기가 holder를 풀 때까지 락을 기다리게 둔다
        ref = publish(AuthoringService(s))
        with psycopg.connect(url, autocommit=True) as holder:
            holder.execute("BEGIN")
            holder.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))

            def blocked_write():
                try:
                    s.add_review({"question_set": ref, "review_id": "rv-y"})
                except BoundaryError:
                    pass
            th = threading.Thread(target=blocked_write)
            th.start()
            wait_until(lambda: holder.execute("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND NOT granted").fetchone()[0] == 1,
                       "쓰기가 락을 기다림")
            assert s.is_ready() is True
            assert th.is_alive()                             # 쓰기는 아직 락을 기다리는 중 = is_ready가 쓰기를 기다리지 않았다
            holder.execute("ROLLBACK")
            th.join(HANG_GUARD_SECONDS)
        s.close()


def test_connect_failure_is_unavailable_without_secrets(monkeypatch):
    """(2) 접속 실패(접속 시한 초과 포함)는 UNAVAILABLE이고 접속 문자열의 비밀이 오류·문맥에 남지 않는다."""
    secret_url = "postgresql://qa:SECRET-dbpw-5e1f@db.invalid:5432/q"

    def fake_migrate(params):
        raise sp.psycopg.OperationalError(f"connection timeout expired {secret_url}")
    monkeypatch.setattr(sp, "migrate_postgres", fake_migrate)
    monkeypatch.setattr(sp.psycopg, "connect", lambda **kw: (_ for _ in ()).throw(sp.psycopg.errors.ConnectionTimeout(f"timeout {secret_url}")))
    with pytest.raises(BoundaryError) as e:
        PostgresQuestionStore(secret_url, connect_timeout=2)
    assert e.value.code == "UNAVAILABLE"
    assert "SECRET-dbpw-5e1f" not in repr(e.value) + str(e.value.to_record()) and e.value.__context__ is None


# ---- 큰 자료 경로의 원자성, 재시작 (E2 보강) ----
def test_max_body_sized_upload_round_trips(validators):
    """서버가 받는 본문 최대(MAX_BODY, 8MiB)가 큰 자료 경로로 저장되고 그대로 읽힌다. 속도는 보지 않는다(문장 상한은 매달림 감시 값)."""
    import hashlib, os
    from question_authoring.components.link_authoring_server import MAX_BODY
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema, statement_timeout=HANG_GUARD_SECONDS, large_statement_timeout=HANG_GUARD_SECONDS)
        for data in (os.urandom(MAX_BODY), b"abc def\n" * (MAX_BODY // 8)):
            h = "sha256:" + hashlib.sha256(data).hexdigest()
            s.put_source(h, "big", data)
            assert s.get_source(h)[1] == data
        s.close()


def test_commit_is_atomic_when_connection_dies_mid_transaction(validators):
    """트랜잭션 도중(버전 삽입 뒤) 서버 쪽에서 연결이 죽으면 반쯤 저장된 버전이 남지 않는다. 재시도가 끝까지 가면 정확히 한 번 저장된다."""
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema)
        svc = AuthoringService(s)
        ref = publish(svc)
        bank = s.get_question_bank(ref)
        bank2 = json.loads(json.dumps(bank)); bank2["version"] = 2; bank2["questions"][0]["name"] = "체온 측정"
        from helpers import bank_hash
        ref2 = {"id": "qs-main", "version": "2", "content_hash": bank_hash(bank2)}
        rv = dict(s.get_reviews(ref)[0], review_id="rv-two", question_set=ref2)

        def commit_dying(always):
            calls = []
            orig = s._write

            def wrapped(fn, large=False):
                def fn2(c):
                    fn(c)                                                       # 삽입이 모두 끝난 뒤, 커밋 직전
                    if always or not calls:
                        calls.append(1)
                        c.execute("SELECT pg_terminate_backend(pg_backend_pid())")
                return orig(fn2, large=large)
            s._write = wrapped
            try:
                s.commit_version(bank2, ref2, rv, None, None)
            finally:
                s._write = orig
        with pytest.raises(BoundaryError) as e:
            commit_dying(always=True)                                           # 두 번 다 죽는다
        assert e.value.code == "UNAVAILABLE"
        assert [v for v, _ in s._version_rows("qs-main")] == [1]                # 2번은 없다
        assert len(s._q("SELECT 1 FROM reviews WHERE review_id='rv-two'")) == 0
        commit_dying(always=False)                                              # 첫 시도만 죽고 재시도가 끝까지 간다
        assert [v for v, _ in s._version_rows("qs-main")] == [1, 2]
        assert len(s._q("SELECT 1 FROM reviews WHERE review_id='rv-two'")) == 1
        s.close()


def test_postgres_restart_keeps_draft_and_confirm_is_idempotent(validators):
    from conftest import example_text
    from question_authoring.components.link_authoring_server import Api
    from question_authoring.wiring.service import Actor
    auth = {"Authorization": "Bearer t"}
    with pg_schema() as (url, schema):
        def boot():
            st = PostgresQuestionStore(url, schema=schema)
            return st, Api(AuthoringService(st), {"t": Actor("dr-kim", "question-author")})
        s1, a1 = boot()
        did = a1.handle("POST", "/v1/materials?question_set_id=qs-main&filename=f.txt", auth, example_text())[1]["draft_id"]
        s1.close()                                                              # 서비스 재시작
        s2, a2 = boot()
        pv = a2.handle("GET", f"/v1/drafts/{did}/preview", auth, b"")[1]
        body = json.dumps({"preview_hash": pv["preview_hash"]}).encode()
        st, ref = a2.handle("POST", f"/v1/drafts/{did}/confirm", auth, body)
        assert st == 200
        s2.close()
        s3, a3 = boot()
        assert a3.handle("POST", f"/v1/drafts/{did}/confirm", auth, body) == (200, ref)
        assert [v for v, _ in s3._version_rows("qs-main")] == [1]
        s3.close()


def test_postgres_purge_retention(validators):
    from datetime import datetime, timedelta, timezone
    from question_authoring.__main__ import purge_once
    old = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema)
        svc = AuthoringService(s, require_two_person=False)
        ref = publish(svc)
        h = s._version_source_hashes()
        assert len(h) == 1
        s.put_source("sha256:" + "d" * 64, "orphan", b"x")
        d = svc.create_draft(AUTHOR_FOR_PG, "qs-old")
        d["updated_at"] = old
        s.save_draft(d)
        s._write(lambda c: c.execute("UPDATE blobs SET created_at=%s", (old,)))
        out = purge_once(s)
        assert out == {"drafts_removed": 1, "sources_removed": 1}
        assert s.load_draft(d["draft_id"]) is None and s.get_source("sha256:" + "d" * 64) is None
        assert s.get_source(next(iter(h))) is not None and s.latest("qs-main") == ref
        assert purge_once(s) == {"drafts_removed": 0, "sources_removed": 0}
        s.close()


def test_postgres_purge_conditional_delete(validators):
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    h = "sha256:" + "9" * 64
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema)
        svc = AuthoringService(s, require_two_person=False)
        s.put_source(h, "f", b"x")
        s._write(lambda c: c.execute("UPDATE blobs SET created_at=%s", (old,)))
        d = svc.create_draft(AUTHOR_FOR_PG, "qs-n"); d["source"] = {"kind": "material", "source_hash": h}; s.save_draft(d)
        assert s._delete_blob_if_stale(h, cutoff) is False and s.get_source(h) is not None      # 초안이 참조한다
        s.put_source(h, "f", b"x")                                                                 # 재업로드: 저장 시각 갱신
        s._delete_draft_if_unchanged(d)
        assert s._delete_blob_if_stale(h, cutoff) is False and s.get_source(h) is not None      # 새 자료로 보인다
        s._write(lambda c: c.execute("UPDATE blobs SET created_at=%s", (old,)))
        assert s._delete_blob_if_stale(h, cutoff) is True and s.get_source(h) is None
        s.close()


# ---- 요청 전체 마감(D120, ADR 0025): 남은 시간이 각 문장의 상한으로 넘어간다(넘긴 값 확인, 시간을 재지 않는다) ----
def test_remaining_request_time_caps_each_statement(validators, monkeypatch):
    monkeypatch.setattr(sp.PostgresQuestionStore, "_abort", staticmethod(lambda c: c.kill()))
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema, statement_timeout=3, large_statement_timeout=30)
        left = [2.5]
        s._remaining = lambda: left[0]
        seen_timers = []

        class RecordingTimer:
            def __init__(self, seconds, fn):
                seen_timers.append(seconds)
                self.daemon = False

            def start(self):
                pass

            def cancel(self):
                pass
        s._timer = RecordingTimer
        fake = FakeConn(lambda conn, sql: type("R", (), {"fetchall": lambda self: [], "fetchone": lambda self: None})())
        s._conn = lambda: fake
        s._q("SELECT 1")                                       # 고정 상한 3+1=4초, 남은 2.5초가 더 짧다 -> 2.5
        s._write(lambda c: None, large=True)                    # 고정 30+1=31초 -> 2.5, 서버 쪽 문장 상한도 2500ms
        left[0] = 100.0
        s._q("SELECT 1")                                       # 남은 시간이 더 길면 고정 상한(4초)
        assert seen_timers == [2.5, 2.5, 4.0]
        assert "SET LOCAL statement_timeout = 2500" in fake.sql
        s._conn = PostgresQuestionStore.__dict__.get("_conn") or s._conn
        s.close()


def test_no_statement_runs_once_the_request_deadline_has_passed(validators):
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema)
        s._remaining = lambda: 0.0
        called = []
        s._conn = lambda: called.append(1)
        with pytest.raises(BoundaryError) as e:
            s._q("SELECT 1")
        assert e.value.code == "UNAVAILABLE" and e.value.retryable and called == []     # 연결도 문장도 시작하지 않았다
        with pytest.raises(BoundaryError):
            s._write(lambda c: None)
        assert called == []
        s.close()
