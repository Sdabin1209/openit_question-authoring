"""3.4.7 저작 화면의 읽기: 문진표 버전 목록(QuestionSetVersions)과 release-gate 판정 조회(Decisions, I4 find_decisions). 후보 제출은 하지 않는다."""
import json

import pytest
from boundary_contracts import PIPELINE_SCHEMA_VERSION, load_schema, validator
from boundary_contracts.errors import BoundaryError

from conftest import example_text
from helpers import AUTHOR, REVIEWER, publish
from question_authoring.components.link_authoring_server import Api
from question_authoring.wiring.service import Actor

H = {"Authorization": "Bearer t-kim"}
# 계약 예시(GateDecision.reject)를 이 시험의 문진표에 맞춘 값. 계약을 통과한다(아래 시험이 확인)
GATE_DECISION = {
 "pipeline_schema_version": PIPELINE_SCHEMA_VERSION,   # 계약 4.0.0: 클라이언트는 다른 MAJOR 레코드를 거부한다
 "decision_id": "gd-0002",
 "decided_at": "2026-10-14T00:00:00Z",
 "gate_policy_version": "1",
 "subject": {
  "question_sets": [
   {
    "id": "qs-v",
    "version": "1",
    "content_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000"
   },
   {
    "id": "qs-alcohol",
    "version": "1",
    "content_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000"
   }
  ],
  "lora_a": {
   "id": "lora-a",
   "version": "1.2.0",
   "content_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000"
  },
  "lora_b": {
   "id": "lora-b",
   "version": "1.0.1",
   "content_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000"
  }
 },
 "runtime_contract_version": 1,
 "result": "REJECT",
 "checks": [
  {
   "kind": "QUESTION_MODEL_COMPAT",
   "result": "FAIL",
   "detail": "문항 4의 단위 코드가 LoRA-A 학습 범위 밖"
  }
 ],
 "reject_reasons": [
  "QUESTION_MODEL_COMPAT 실패"
 ],
 "approver": {
  "role": "gate-service"
 },
 "reject_codes": [
  "CHECK_FAILED"
 ]
}


class FakeGate:
    def __init__(self, items=None, boom=None):
        self.calls, self.items, self.boom = [], items or [], boom

    def find_decisions(self, **kw):
        self.calls.append(kw)
        if self.boom:
            raise self.boom
        return self.items, None


def api_of(svc, gate=None):
    return Api(svc, {"t-kim": Actor("dr-kim", "question-author")}, gate=gate)


def test_versions_lists_titles_and_reviewed_flag_and_passes_the_contract(svc):
    publish(svc, "qs-v")
    st, out = api_of(svc).handle("GET", "/v1/set-versions?question_set_id=qs-v", H)
    assert st == 200 and out["next_cursor"] is None and len(out["items"]) == 1
    it = out["items"][0]
    assert it["question_set_id"] == "qs-v" and it["version"] == "1" and it["title"] == "예시 문진표" and it["reviewed"] is True
    assert not list(validator("AuthoringResponses").evolve(schema={"$ref": "#/$defs/QuestionSetVersions"}).iter_errors(out))


def test_versions_pagination_and_all_sets(svc):
    publish(svc, "qs-a"); publish(svc, "qs-b")
    a = api_of(svc)
    st, p1 = a.handle("GET", "/v1/set-versions?limit=1", H)
    assert st == 200 and [i["question_set_id"] for i in p1["items"]] == ["qs-a"] and p1["next_cursor"] == "qs-a@1"
    st, p2 = a.handle("GET", "/v1/set-versions?limit=1&cursor=qs-a@1", H)
    assert [i["question_set_id"] for i in p2["items"]] == ["qs-b"] and p2["next_cursor"] is None


def test_versions_needs_a_valid_token_and_valid_query(svc):
    a = api_of(svc)
    assert a.handle("GET", "/v1/set-versions", {})[0] == 401
    assert a.handle("GET", "/v1/set-versions?limit=0", H)[0] == 400
    assert a.handle("GET", "/v1/set-versions?bogus=1", H)[0] == 400


def test_fixture_is_a_valid_gate_decision():
    assert not list(validator("GateDecision").iter_errors(GATE_DECISION))


def test_decisions_reads_find_decisions_for_the_version_and_returns_gate_decisions(svc):
    g = FakeGate([GATE_DECISION])
    st, out = api_of(svc, g).handle("GET", "/v1/set-decisions?question_set_id=qs-v&version=1", H)
    assert st == 200 and out == {"items": [GATE_DECISION], "next_cursor": None}
    assert g.calls == [{"question_set": {"id": "qs-v", "version": "1"}, "cursor": None, "limit": 50}]


def test_decisions_query_without_version_asks_by_set_only(svc):
    g = FakeGate([])
    assert api_of(svc, g).handle("GET", "/v1/set-decisions?question_set_id=qs-v", H)[0] == 200
    assert g.calls[0]["question_set"] == {"id": "qs-v", "version": None}


def test_decisions_without_gate_configured_is_412_not_an_invented_empty_list(svc):
    st, out = api_of(svc, None).handle("GET", "/v1/set-decisions?question_set_id=qs-v", H)
    assert st == 412 and out["code"] == "FAILED_PRECONDITION" and "QA_GATE_URL" in out["message"]


@pytest.mark.parametrize("err,status,code", [(BoundaryError("UNAVAILABLE", "x"), 503, "UNAVAILABLE"),
                                             (BoundaryError("PERMISSION_DENIED", "x", {"http_status": 403}), 403, "PERMISSION_DENIED"),
                                             (BoundaryError("INTEGRITY_ERROR", "x"), 503, "UNAVAILABLE")])
def test_decisions_upstream_failures_keep_their_meaning(svc, err, status, code):
    st, out = api_of(svc, FakeGate(boom=err)).handle("GET", "/v1/set-decisions?question_set_id=qs-v", H)
    assert st == status and out["code"] == code


def test_decisions_requires_the_question_set_id_and_never_submits(svc):
    g = FakeGate()
    a = api_of(svc, g)
    assert a.handle("GET", "/v1/set-decisions", H)[0] == 400
    assert a.handle("POST", "/v1/set-decisions?question_set_id=qs-v", H, b"{}")[0] in (404, 405)
    assert not hasattr(g, "submit_candidate") and g.calls == []


# ---- 계약 5.0.0(결함 대장 #10, D117): 입구 상한은 계약 쿼리 스키마가 맡는다 ----
def test_decisions_version_over_128_is_a_400_at_the_entrance_and_never_reaches_the_gate(svc):
    # 4.x 에서는 이 값이 release-gate 까지 가서 거절되고 503 으로 보였다. 지금은 입구에서 400 이다(503 이 아니다)
    g = FakeGate([])
    a = api_of(svc, g)
    st, out = a.handle("GET", "/v1/set-decisions?question_set_id=qs-v&version=" + "1" * 129, H)
    assert st == 400 and out["code"] == "INVALID_RECORD" and g.calls == []
    assert a.handle("GET", "/v1/set-decisions?question_set_id=qs-v&version=" + "1" * 128, H)[0] == 200   # 경계값은 통과
    assert len(g.calls) == 1


def test_decisions_gate_empty_string_next_cursor_is_sent_as_null(svc):
    # 계약 5.0.0: Decisions 는 HttpPages.Decisions 별칭이라 next_cursor 빈 문자열을 거부한다. 출구는 null 로 낸다(503 이 되지 않는다)
    class EmptyCursor(FakeGate):
        def find_decisions(self, **kw):
            return [GATE_DECISION], ""
    st, out = api_of(svc, EmptyCursor()).handle("GET", "/v1/set-decisions?question_set_id=qs-v", H)
    assert st == 200 and out["next_cursor"] is None


def test_the_contract_decisions_schema_rejects_an_empty_next_cursor():
    v = validator("AuthoringResponses").evolve(schema={"$ref": "#/$defs/Decisions"})
    assert not list(v.iter_errors({"items": [], "next_cursor": None}))
    assert list(v.iter_errors({"items": [], "next_cursor": ""}))


def test_overlong_set_versions_cursor_and_unknown_limit_are_400(svc):
    a = api_of(svc)
    assert a.handle("GET", "/v1/set-versions?cursor=" + "x" * 513, H)[0] == 400
    assert a.handle("GET", "/v1/set-decisions?question_set_id=qs-v&limit=501", H)[0] == 400


def test_a_stored_4x_record_is_not_silently_mixed_into_5x_answers(svc):
    # 계약 5.0.0 '새 환경 전제': 4.x 로 저장된 검수 기록은 읽을 때 UNSUPPORTED_VERSION 으로 실패한다(승인 판정에 쓰이지도, 응답에 실리지도 않는다)
    publish(svc, "qs-old")
    reader = svc.store.reader()
    ref = reader.ref_of("qs-old", "1")
    assert reader.get_reviews(ref)                      # 지금 MAJOR 의 기록은 읽힌다
    real = svc.store._b._review_rows
    svc.store._b._review_rows = lambda q, n: [{**r, "pipeline_schema_version": "4.0"} for r in real(q, n)]
    with pytest.raises(BoundaryError) as e:
        reader.get_reviews(ref)
    assert e.value.code == "UNSUPPORTED_VERSION"
    with pytest.raises(BoundaryError) as e:
        reader.latest("qs-old", reviewed_only=True)
    assert e.value.code == "UNSUPPORTED_VERSION"
