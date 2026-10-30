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
import subprocess
import threading
import time
from urllib.parse import urlparse

BLACKHOLE = "postgresql://qa:SECRET-dbpw-5e1f@10.255.255.1:5432/q"   # 응답하지 않는 주소(SYN을 버린다)


def _container_of(url):
    """TEST_DATABASE_URL의 호스트 포트를 게시한 docker 컨테이너 이름. docker가 없거나 못 찾으면 SKIP."""
    port = urlparse(url).port
    try:
        rows = subprocess.run(["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"],
                              capture_output=True, text=True, timeout=20).stdout.splitlines()
        out = [r.split("\t")[0] for r in rows if f":{port}->5432" in r]
    except (OSError, subprocess.TimeoutExpired):
        out = []
    if not out:
        pytest.skip("TEST_DATABASE_URL의 컨테이너를 docker에서 찾지 못해 pause 시험 건너뜀")
    return out[0]


@pytest.fixture
def paused(request):
    """docker pause로 PostgreSQL을 얼린다(TCP는 받되 응답이 없다). 끝나면 반드시 되돌린다."""
    state = {"name": None}

    def pause(url):
        state["name"] = _container_of(url)
        subprocess.run(["docker", "pause", state["name"]], check=True, capture_output=True, timeout=30)

    def resume():
        if state["name"]:
            subprocess.run(["docker", "unpause", state["name"]], capture_output=True, timeout=30)
            state["name"] = None
    request.addfinalizer(resume)
    pause.resume = resume
    return pause


def test_timeout_settings_are_validated():
    for bad in (0, -1, 601, "3"):
        with pytest.raises(ValueError):
            PostgresQuestionStore("postgresql://u@h/d", connect_timeout=bad)
    with pytest.raises(ValueError):
        PostgresQuestionStore("postgresql://u@h/d", probe_timeout=0)


def test_server_side_statement_timeout_is_set(validators):
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema, statement_timeout=2)
        assert s._q("SHOW statement_timeout")[0][0] == "2s"
        s.close()


def test_slow_statement_is_cut_by_server_timeout(validators):
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema, statement_timeout=1)
        t = time.monotonic()
        with pytest.raises(BoundaryError) as e:
            s._q("SELECT pg_sleep(30)")
        assert e.value.code == "UNAVAILABLE" and time.monotonic() - t < 3
        assert s._q("SELECT 1") == [(1,)]
        s.close()


def test_lock_wait_in_write_is_bounded(validators):
    """다른 세션이 쓰기 직렬화 락을 쥐고 있으면 쓰기는 statement_timeout 안에 UNAVAILABLE이 된다. 읽기는 영향이 없다."""
    from question_authoring.components.store_postgres import _LOCK_KEY
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema, statement_timeout=1)
        ref = publish(AuthoringService(s))
        with psycopg.connect(url, autocommit=True) as holder:
            holder.execute("BEGIN")
            holder.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
            t = time.monotonic()
            with pytest.raises(BoundaryError) as e:
                s.add_review({"question_set": ref, "review_id": "rv-x"})
            assert e.value.code == "UNAVAILABLE" and time.monotonic() - t < 3
            assert s.latest("qs-main") == ref
            holder.execute("ROLLBACK")
        s.close()


def test_readyz_probe_does_not_wait_for_main_connection_or_lock(validators):
    """주 연결이 쓰기(락 대기)에 묶여 있어도 is_ready는 즉시 True다."""
    from question_authoring.components.store_postgres import _LOCK_KEY
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema, statement_timeout=3)
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
            time.sleep(0.5)                                  # 쓰기가 self._lock과 주 연결을 쥔 채 락을 기다리는 중
            t = time.monotonic()
            assert s.is_ready() is True
            assert time.monotonic() - t < 1.0
            holder.execute("ROLLBACK")
            th.join(10)
        s.close()


def test_readyz_503_within_limit_when_unreachable_then_recovers(validators):
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema)
        assert s.is_ready() is True
        good = s._url
        s._url = BLACKHOLE
        t = time.monotonic()
        assert s.is_ready() is False
        assert time.monotonic() - t < 3.0                    # 기본 2초 + 여유
        s._url = good
        assert s.is_ready() is True
        s.close()


def test_connect_is_bounded_when_unreachable():
    t = time.monotonic()
    with pytest.raises(BoundaryError) as e:
        PostgresQuestionStore(BLACKHOLE, connect_timeout=2)
    assert e.value.code == "UNAVAILABLE" and time.monotonic() - t < 4
    assert "SECRET-dbpw-5e1f" not in repr(e.value) + str(e.value.to_record()) and e.value.__context__ is None


def test_frozen_server_readyz_503_and_request_path_bounded_then_recovers(validators, paused):
    """docker pause: TCP는 받되 응답이 없다. 주 연결이 이미 열려 있는 경우(요청 경로)와 새 접속(/readyz 확인) 모두."""
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema, statement_timeout=2, probe_timeout=2)
        ref = publish(AuthoringService(s))
        paused(url)
        try:
            t = time.monotonic()
            assert s.is_ready() is False
            probe_secs = time.monotonic() - t
            assert probe_secs < 3.0, probe_secs
            t = time.monotonic()
            with pytest.raises(BoundaryError) as e:         # 열려 있던 주 연결로 읽기: 서버가 얼어 서버 쪽 timeout은 못 듣는다 -> 클라이언트가 끊는다
                s.latest("qs-main")
            read_secs = time.monotonic() - t
            assert e.value.code == "UNAVAILABLE" and read_secs < 2 + 1 + 1.5, read_secs
            t = time.monotonic()
            with pytest.raises(BoundaryError):               # 새 접속도 connect_timeout 안에 포기
                s.latest("qs-main")
            assert time.monotonic() - t < 3 + 1.5
        finally:
            paused.resume()
        deadline = time.monotonic() + 15                     # 복구: 서비스를 다시 띄우지 않아도 다시 쓸 수 있다
        while not s.is_ready() and time.monotonic() < deadline:
            time.sleep(0.5)
        assert s.is_ready() is True
        assert s.latest("qs-main") == ref
        s.close()


# ---- 큰 자료 경로의 별도 상한, 원자성, 재시작 (E2 보강) ----
def _slow_blob_trigger(c, ms):
    c.execute(f"CREATE FUNCTION slow_blob() RETURNS trigger AS $$ BEGIN PERFORM pg_sleep({ms / 1000}); RETURN NEW; END $$ LANGUAGE plpgsql")
    c.execute("CREATE TRIGGER slow_blob BEFORE INSERT ON blobs FOR EACH ROW EXECUTE FUNCTION slow_blob()")


def test_large_path_has_its_own_statement_limit(validators):
    """원본 자료 삽입이 일반 문장 상한(여기 0.5초)보다 오래 걸려도 큰 자료 상한 안이면 성공한다. 같은 지연의 일반 쓰기는 끊긴다."""
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema, statement_timeout=0.5, large_statement_timeout=5)
        with psycopg.connect(url, autocommit=True, options=f"-c search_path={schema}") as c:
            _slow_blob_trigger(c, 1200)
        t = time.monotonic()
        s.put_source("sha256:" + "a" * 64, "f.txt", b"data")                    # 1.2초 걸려도 성공
        assert 1.1 < time.monotonic() - t < 4
        assert s.get_source("sha256:" + "a" * 64) == ("f.txt", b"data")
        with pytest.raises(BoundaryError) as e:                                  # 큰 자료 경로가 아닌 문장은 0.5초에 끊긴다
            s._write(lambda c: c.execute("SELECT pg_sleep(1.2)"))
        assert e.value.code == "UNAVAILABLE"
        s2 = PostgresQuestionStore(url, schema=schema, statement_timeout=0.5, large_statement_timeout=0.6)
        with pytest.raises(BoundaryError) as e:                                  # 큰 자료 상한도 무한이 아니다
            s2.put_source("sha256:" + "b" * 64, "g.txt", b"x")
        assert e.value.code == "UNAVAILABLE"
        assert s.get_source("sha256:" + "b" * 64) is None                        # 끊긴 쓰기는 남지 않는다
        s.close(); s2.close()


def test_max_body_sized_upload_fits_default_limit(validators):
    """서버가 받는 본문 최대(MAX_BODY, 8MiB)를 기본 상한(문장 3초)으로 삽입한다. 정상 업로드가 끊기지 않아야 한다."""
    import hashlib, os
    from question_authoring.components.link_authoring_server import MAX_BODY
    with pg_schema() as (url, schema):
        s = PostgresQuestionStore(url, schema=schema)
        for data in (os.urandom(MAX_BODY), b"abc def\n" * (MAX_BODY // 8)):
            h = "sha256:" + hashlib.sha256(data).hexdigest()
            t = time.monotonic()
            s.put_source(h, "big", data)
            assert time.monotonic() - t < 3.0
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
