"""사람용 요청 전체 마감(D120, ADR 0025). 시험이 쥔 가짜 시계로 마감을 흘린다. 실제 시간은 기다리지도 재지도 않는다."""
import json

import pytest
from boundary_contracts.errors import BoundaryError

from conftest import example_text
from helpers import AUTHOR, FakeMonotonic
from question_authoring.components.link_authoring_server import Api
from question_authoring.wiring.deadline import DEFAULT_REQUEST_DEADLINE_SECONDS, RequestDeadline
from question_authoring.wiring.service import Actor, AuthoringService

AUTH = {"Authorization": "Bearer t"}
MAT = "/v1/materials?question_set_id=qs-main&filename=f.txt"


def test_default_deadline_is_inside_the_gateway_header_limit():
    """기본 마감 12초 < gateway 응답 머리글 상한 13초(deploy-exposure.json). 설정 값끼리 비교한다."""
    import pathlib
    decl = json.loads((pathlib.Path(__file__).resolve().parent.parent / "deploy-exposure.json").read_text(encoding="utf-8"))
    header_ms = [r["timeouts_ms"]["response_header"] for r in decl["routes"] if r["auth"]["kind"] == "account"]
    assert header_ms and all(DEFAULT_REQUEST_DEADLINE_SECONDS * 1000 < h for h in header_ms)


def test_deadline_scope_remaining_and_cap():
    clock = FakeMonotonic()
    d = RequestDeadline(clock, 12.0)
    assert d.remaining() is None                       # 요청 범위 밖: 마감 없음
    with d.scope():
        assert d.remaining() == 12.0
        clock.advance(11.0)
        assert d.remaining() == 1.0
        d.check("지금")                                  # 남았으면 진행
        with d.scope():                                # 중첩 범위가 마감을 늘리지 못한다
            assert d.remaining() == 1.0
        clock.advance(1.0)
        with pytest.raises(BoundaryError) as e:
            d.check("쓰기")
        assert e.value.code == "UNAVAILABLE" and e.value.retryable
    assert d.remaining() is None


def _api(store, clock, seconds=12.0):
    d = RequestDeadline(clock, seconds)
    svc = AuthoringService(store, deadline=d, require_two_person=False)
    return svc, Api(svc, {"t": Actor("dr-kim", "question-author")}, deadline=d)


class AdvancingStore:
    """저장소 대역 래퍼: 지정한 연산을 부르면 시험 시계를 흘린다(가짜 의존이 시계를 흘림)."""
    def __init__(self, inner, clock, op, secs):
        self._inner, self._clock, self._op, self._secs = inner, clock, op, secs

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if name != self._op:
            return attr

        def hooked(*a, **k):
            r = attr(*a, **k)
            self._clock.advance(self._secs)
            return r
        return hooked


def test_request_within_the_deadline_completes(store):
    clock = FakeMonotonic()
    svc, api = _api(store, clock)
    st, out = api.handle("POST", MAT, AUTH, example_text())
    assert st == 200 and out["status"] == "PARSED"


def test_upload_past_the_deadline_is_503_retryable_and_leaves_no_draft_then_retry_succeeds(store):
    """원본 자료 저장에 13초가 걸렸다(가짜 의존이 시계를 흘림) -> 초안을 저장하지 않고 503, 같은 요청을 다시 보내면 정상(멱등)."""
    clock = FakeMonotonic()
    svc, api = _api(AdvancingStore(store, clock, "put_source", 13.0), clock)
    st, out = api.handle("POST", MAT, AUTH, example_text())
    assert st == 503 and out["code"] == "UNAVAILABLE" and out["retryable"] is True
    assert store.list_drafts() == []                                   # 반쯤 쓴 초안이 없다
    clock.advance(1000.0)                                              # 다음 요청은 새 마감을 받는다
    svc2, api2 = _api(store, clock)
    st, out = api2.handle("POST", MAT, AUTH, example_text())
    assert st == 200 and out["status"] == "PARSED" and len(store.list_drafts()) == 1


def test_confirm_past_the_deadline_commits_nothing_and_retry_keeps_the_reserved_number(store):
    clock = FakeMonotonic()
    svc, api = _api(store, clock)
    st, out = api.handle("POST", MAT, AUTH, example_text())
    did = out["draft_id"]
    ph = api.handle("GET", f"/v1/drafts/{did}/preview", AUTH)[1]["preview_hash"]
    slow_svc, slow_api = _api(AdvancingStore(store, clock, "save_draft", 13.0), clock)   # 번호 예약 저장이 13초 걸린다
    st, out = slow_api.handle("POST", f"/v1/drafts/{did}/confirm", AUTH, json.dumps({"preview_hash": ph}).encode())
    assert st == 503 and out["retryable"] is True
    assert store.reader().list_versions("qs-main")[0] == []           # 버전이 저장되지 않았다
    st, ref = api.handle("POST", f"/v1/drafts/{did}/confirm", AUTH, json.dumps({"preview_hash": ph}).encode())
    assert st == 200 and ref["version"] == "1"                         # 다시 보내면 같은 번호로 끝난다


def test_entry_point_passes_the_deadline_setting(monkeypatch, tmp_path):
    from question_authoring import __main__ as qa_main
    monkeypatch.setenv("QA_DB_PATH", str(tmp_path / "q.db"))
    api, store = qa_main.build()
    try:
        assert api.deadline.seconds == 12.0
    finally:
        store.close()
    monkeypatch.setenv("QA_REQUEST_DEADLINE_SECONDS", "7.5")
    api, store = qa_main.build()
    try:
        assert api.deadline.seconds == 7.5
    finally:
        store.close()
