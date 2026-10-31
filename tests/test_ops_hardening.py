"""전수 점검 P6(운영 구성 거부), P7(자원 한도, SIGTERM), P4/D88 U4(보존·정리), M-6(v2 Trace 문항 ID)."""
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from conftest import example_text
from helpers import AUTHOR
from question_authoring.__main__ import build, check_environment, main, purge_once
from helpers import stop_http
from question_authoring.components.link_authoring_server import Api, make_server
from question_authoring.wiring.service import Actor, AuthoringService
from question_authoring.components.signals_build import question_ids_from_trace
from question_authoring.wiring.stores import MemoryQuestionStore, SqliteQuestionStore


# ---- P6 ----
def test_production_refuses_to_start_without_db_url_or_tokens(monkeypatch, tmp_path):
    monkeypatch.delenv("QA_DEV_MODE", raising=False)
    for k in ("QA_DATABASE_URL", "QA_DATABASE_URL_FILE", "QA_AUTHOR_TOKENS", "QA_AUTHOR_TOKENS_FILE", "QA_GATEWAY_TOKENS", "QA_GATEWAY_TOKENS_FILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("QA_DB_PATH", str(tmp_path / "q.db"))
    with pytest.raises(SystemExit) as e:
        build()
    assert "QA_DATABASE_URL" in str(e.value)
    monkeypatch.setenv("QA_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/d")     # URL이 있어도 토큰이 없으면 거부(DB 접속 전에 판단)
    with pytest.raises(SystemExit) as e:
        build()
    assert "토큰" in str(e.value)
    assert not (tmp_path / "q.db").exists()                                      # 조용히 SQLite를 만들지 않았다


def test_dev_mode_allows_sqlite_and_no_tokens(monkeypatch, tmp_path):
    monkeypatch.setenv("QA_DB_PATH", str(tmp_path / "q.db"))
    monkeypatch.delenv("QA_DATABASE_URL", raising=False)
    api, store = build()
    assert api.handle("GET", "/readyz", {})[0] == 200
    store.close()


def test_roles_and_two_person_flag_are_validated(monkeypatch, tmp_path):
    monkeypatch.setenv("QA_DB_PATH", str(tmp_path / "q.db"))
    monkeypatch.setenv("QA_AUTHOR_TOKENS", "t:dr-kim:superuser")
    with pytest.raises(SystemExit):
        build()
    monkeypatch.setenv("QA_AUTHOR_TOKENS", "t:dr-kim:question-author")
    monkeypatch.setenv("QA_REQUIRE_TWO_PERSON", "maybe")
    with pytest.raises(SystemExit):
        build()
    monkeypatch.setenv("QA_REQUIRE_TWO_PERSON", "false")
    api, store = build()
    st, body = api.handle("GET", "/readyz", {})
    assert st == 200 and body["warnings"] == ["two_person_disabled"]
    store.close()


# ---- P7 ----
# 실제 SIGTERM으로 프로세스가 끝나는가는 신호 처리·waitress·운영체제의 동작이라 영역 시험이 아니다(U421 정정, deploy smoke/E2E의 몫).
# 종료 절차의 우리 몫(drain, close_all, 서버에 넘기는 값)은 tests/test_server_properties.py에서 시간을 재지 않고 확인한다.
def test_sigterm_handler_is_installed_before_the_server_starts_listening():
    """종료 신호 처리기를 서버 기동 안내(`시작` 줄)보다 먼저 건다: 기동 직후 신호가 와도 기본 동작(즉시 종료·무시)으로 빠지지 않는다."""
    import inspect
    src = inspect.getsource(main)
    assert src.index("signal.signal(signal.SIGTERM, stop)") < src.index('print("question-authoring 시작"')


# ---- P4 / D88 U4 ----
def _old(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(params=["memory", "sqlite"])
def store2(request):
    s = MemoryQuestionStore() if request.param == "memory" else SqliteQuestionStore()
    yield s
    s.close()


def test_retention_purge(store2, validators, monkeypatch):
    s = AuthoringService(store2, require_two_person=False)
    k = AUTHOR
    confirmed = s.ingest_material(k, "qs-a", "f.txt", example_text())["draft_id"]
    s.confirm(k, confirmed, s.get_preview(confirmed)["preview_hash"])           # 버전이 이 원본을 참조한다
    old_open = s.ingest_material(k, "qs-b", "f.txt", example_text().replace("체온".encode(), "혈압".encode()))["draft_id"]
    fresh = s.create_draft(k, "qs-c")["draft_id"]
    orphan_hash = "sha256:" + "e" * 64
    store2.put_source(orphan_hash, "orphan.txt", b"x")                            # 어디에도 참조되지 않는 원본
    # 오래된 것처럼 만든다
    d = store2.load_draft(old_open); d["updated_at"] = _old(40); store2.save_draft(d)
    if hasattr(store2, "_blob_times"):
        store2._b._blob_times = {h: _old(40) for h in store2._blob_times}
    else:
        store2._db.execute("UPDATE blobs SET created_at=?", (_old(40),))
    out = purge_once(store2)
    assert out["drafts_removed"] == 1 and store2.load_draft(old_open) is None
    assert store2.load_draft(fresh) is not None and store2.load_draft(confirmed) is not None   # 최근 초안과 확정된 초안은 남는다
    assert store2.get_source(orphan_hash) is None                                  # 고아 원본은 정리
    h = s.get_draft(confirmed)["source"]["source_hash"]
    assert store2.get_source(h) is not None                                        # 버전이 참조하는 원본은 남는다
    assert len(store2.reader().list_versions("qs-a")[0]) == 1                      # 버전·검수는 지우지 않는다
    assert purge_once(store2) == {"drafts_removed": 0, "sources_removed": 0}       # 멱등


def test_purge_stamps_unknown_age_and_waits(store2):
    store2.put_source("sha256:" + "f" * 64, "f", b"x")
    if hasattr(store2, "_blob_times"):
        store2._blob_times["sha256:" + "f" * 64] = None
    else:
        store2._db.execute("UPDATE blobs SET created_at=NULL")
    assert purge_once(store2)["sources_removed"] == 0                              # 나이를 모르면 이번에 시각을 적고 기간이 지난 뒤 지운다
    assert store2.get_source("sha256:" + "f" * 64) is not None


def test_retention_rejects_indefinite(monkeypatch):
    for bad in ("0", "-1", "none", "null", "abc"):
        monkeypatch.setenv("QA_DRAFT_RETENTION_DAYS", bad)
        with pytest.raises(SystemExit):
            check_environment()   # 진입점이 purge 전에 하는 검사(계약 Environment)


# ---- M-6 ----
def test_question_ids_from_v2_trace():
    v2 = {"session_id": "S", "turn_id": "1", "extract_input": {"question_id": "Q4", "current_question": "?", "user_utterance": "<MASKED>", "state": {}},
          "extract_output": {"values": [], "action": "NEXT", "next_item": "Q5"}}
    assert question_ids_from_trace(v2) == {"Q4"}
    assert question_ids_from_trace({"extract_input": None, "extract_output": None}) == set()
    assert question_ids_from_trace({"extract_input": {"question_id": ""}}) == set()


# ---- 라운드2 재점검 ----
def test_purge_cli_follows_production_config_check(monkeypatch, tmp_path):
    monkeypatch.delenv("QA_DEV_MODE", raising=False)
    monkeypatch.delenv("QA_DATABASE_URL", raising=False)
    monkeypatch.delenv("QA_DATABASE_URL_FILE", raising=False)
    monkeypatch.setenv("QA_DB_PATH", str(tmp_path / "q.db"))
    with pytest.raises(SystemExit) as e:
        main(["purge"])
    assert "QA_DATABASE_URL" in str(e.value) and not (tmp_path / "q.db").exists()   # 조용히 빈 SQLite를 열지 않았다
    monkeypatch.setenv("QA_DEV_MODE", "1")
    assert main(["purge"]) == 0


def test_purge_does_not_delete_source_referenced_by_a_new_draft(store2, validators):
    s = AuthoringService(store2, require_two_person=False)
    h = "sha256:" + "c" * 64
    store2.put_source(h, "old.txt", b"data")
    if hasattr(store2, "_blob_times"):
        store2._blob_times[h] = _old(40)
    else:
        store2._db.execute("UPDATE blobs SET created_at=?", (_old(40),))
    orig = store2._blob_meta

    def meta_then_new_upload():
        rows = orig()                                  # purge는 "오래된 미참조 자료"를 이미 골랐다
        store2.put_source(h, "old.txt", b"data")        # 그 뒤 같은 자료가 다시 올라온다(저장 시각 갱신)
        d = s.create_draft(AUTHOR, "qs-new")
        d["source"] = {"kind": "material", "source_hash": h}
        store2.save_draft(d)                           # 새 초안이 참조한다
        return rows
    store2._blob_meta = meta_then_new_upload
    assert purge_once(store2)["sources_removed"] == 0
    assert store2.get_source(h) is not None


def test_purge_conditional_delete_checks_reference_atomically(store2, validators):
    """저장 시각이 아직 오래돼도(재업로드 없이) 새 초안이 참조하면 조건부 삭제가 거부한다."""
    s = AuthoringService(store2, require_two_person=False)
    h = "sha256:" + "b" * 64
    store2.put_source(h, "f", b"x")
    if hasattr(store2, "_blob_times"):
        store2._blob_times[h] = _old(40)
    else:
        store2._db.execute("UPDATE blobs SET created_at=?", (_old(40),))
    d = s.create_draft(AUTHOR, "qs-n"); d["source"] = {"kind": "material", "source_hash": h}; store2.save_draft(d)
    assert store2._delete_blob_if_stale(h, _old(30)) is False and store2.get_source(h) is not None
    store2.save_draft(dict(d, status="CONFIRMED"))                                   # 초안이 확정되어도 참조는 남는다
    assert store2._delete_blob_if_stale(h, _old(30)) is False


def test_purge_threaded_with_uploads_never_loses_a_referenced_source(validators):
    store = SqliteQuestionStore()
    s = AuthoringService(store, require_two_person=False)
    lost = []
    stop = threading.Event()
    # 시험의 가정(라운드3 조사): 보존 기간(기본 30일) 안의 새 업로드는 purge 대상이 아니다. 아래 UPDATE가 방금 올린 참조 전 원본까지
    # '오래된 것'으로 만들면 업로드~초안 저장 사이의 합법적인 틈(원본은 있고 참조는 아직 없음)에서 purge가 지워 시험이 간헐 실패한다(코드 결함 아님).
    # 그래서 '올림~참조 저장' 구간과 '나이 올리기'만 서로 배타적으로 하고, purge_once 자체는 업로드와 자유롭게 경쟁시킨다.
    inflight = threading.Lock()

    def uploader():
        i = 0
        while not stop.is_set() and i < 150:
            i += 1
            data = b"same material"
            h = "sha256:" + "a" * 64
            with inflight:
                store.put_source(h, "f", data)
                d = s.create_draft(AUTHOR, "qs-x"); d["source"] = {"kind": "material", "source_hash": h}; store.save_draft(d)
            if store.get_source(h) is None:
                lost.append(i)
    t = threading.Thread(target=uploader); t.start()
    for _ in range(150):
        with inflight:
            store._db.execute("UPDATE blobs SET created_at=?", (_old(40),))   # 항상 오래된 것처럼 보이게 해 경쟁을 키운다
        purge_once(store)
        with inflight:   # 진행 중인 업로드가 없는 순간: 참조하는 초안이 있으면 원본은 반드시 있어야 한다(purge가 참조된 원본을 지우면 여기서 잡힌다)
            if store.list_drafts("qs-x") and store.get_source("sha256:" + "a" * 64) is None:
                lost.append("after-purge")
    stop.set(); t.join()
    assert not lost
    store.close()


def test_gateway_email_style_user_ids(validators):
    from question_authoring.components.link_authoring_server import Api
    s = AuthoringService(MemoryQuestionStore())
    api = Api(s, {}, gateway_tokens={"gw"}, user_roles={"kim@hosp.kr": "question-author", "park.md@hosp.kr": "clinical-reviewer"})

    def call(user, method="GET", path="/v1/drafts", body=b""):
        h = {"Authorization": "Bearer gw", "X-Openit-User": user}
        return api.handle(method, path, h, body)
    assert call("kim@hosp.kr")[0] == 200 and call("park.md@hosp.kr")[0] == 200
    st, out = call("kim@hosp.kr", "POST", "/v1/materials?question_set_id=qs-e&filename=f.txt", example_text())
    assert st == 200
    did = out["draft_id"]
    ph = call("kim@hosp.kr", "GET", f"/v1/drafts/{did}/preview")[1]["preview_hash"]
    st, ref = call("kim@hosp.kr", "POST", f"/v1/drafts/{did}/confirm", json.dumps({"preview_hash": ph}).encode())
    assert st == 200
    assert s.store.get_reviews(ref)[0]["reviewer_id"] == "kim:at:hosp.kr"          # 계약 Id에 맞게 기록된다
    st, _ = call("park.md@hosp.kr", "POST", "/v1/reviews", json.dumps(
        {"question_set": ref, "decision": "APPROVED", "clinical_basis_approved": True}).encode())
    assert st == 200 and s.store.reader().latest("qs-e", reviewed_only=True) == ref
    # 안전하지 않은 형식은 모두 거부
    api.user_roles.update({"a b@x": "question-author", "a@b@c": "question-author", "x:y": "question-author",
                           "<s>@x": "question-author", "a%40b": "question-author", ("a" * 70 + "@x"): "question-author"})
    for bad in ("a b@x", "a@b@c", "x:y", "<s>@x", "a%40b", "a" * 70 + "@x", "@x", "kim@"):
        assert call(bad)[0] == 403, bad


def test_commit_refuses_a_version_whose_source_material_is_gone(store, validators):
    """L24: 원본이 정리된 뒤의 확정은 출처 없는 버전을 만들지 않는다. 다시 올리면 확정된다(모든 저장소 구현이 같다)."""
    from boundary_contracts.errors import BoundaryError
    svc = AuthoringService(store, require_two_person=False)
    out = svc.ingest_material(AUTHOR, "qs-g", "form.txt", example_text())
    did = out["draft_id"]
    h = store.load_draft(did)["source"]["source_hash"]
    ph = svc.get_preview(did)["preview_hash"]
    # 원본만 사라진 상태를 만든다(저장소 구현별 내부 삭제): purge와 같은 결과
    if store._b.__class__.__name__ == "MemoryQuestionStore":
        store._blobs.pop(h)
    elif getattr(store, "kind", "") == "postgres":
        store._write(lambda c: c.execute("DELETE FROM blobs"))
    else:
        store._db.execute("DELETE FROM blobs")
    with pytest.raises(BoundaryError) as e:
        svc.confirm(AUTHOR, did, ph)
    assert e.value.code == "FAILED_PRECONDITION"
    with pytest.raises(BoundaryError) as nf:
        store.latest("qs-g")
    assert nf.value.code == "NOT_FOUND"
    store.put_source(h, "form.txt", example_text())
    assert svc.confirm(AUTHOR, did, ph)["version"] == "1"
