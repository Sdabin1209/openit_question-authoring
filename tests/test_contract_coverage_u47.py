"""U47 (6)(7): 저작 API의 모든 응답이 계약 AuthoringResponses·SignalsResponse를, 저장된 ReviewRecord/ChangeClassification의
ext["question-authoring"]가 계약 QuestionAuthoringExt를 통과하는지 시험으로 본다(실행 코드의 자기 검사와 별개로, 계약 쪽에서 보는 검증).
이미 있던 일부는 tests/test_contract_u3.py(Draft, Preview, DraftList, ConfirmResponse, 빈 SignalsResponse)이고, 여기서 나머지(MaterialIngested 두 모양,
AddReviewResponse, 수정·확인·버림 뒤 Draft, OK 모양 SignalsResponse, 저장된 ext 전부)를 채운다."""
import json

import boundary_contracts as bc
import pytest
from boundary_contracts.clients import HttpFailureStore

from conftest import example_text
from question_authoring.components.link_authoring_server import Api
from question_authoring.wiring.service import Actor
from test_signals import i7  # noqa: F401  (ops-feedback 대역 fixture)

H = {"Authorization": "Bearer tok-a"}
REV = {"Authorization": "Bearer tok-r"}


def sub(schema, d):
    v = bc.validator(schema)
    return v.evolve(schema={"$ref": f"#/$defs/{d}"}) if d else v


def ok(schema, d, value):
    errs = [f"{'/'.join(map(str, e.absolute_path))}: {e.message[:80]}" for e in sub(schema, d).iter_errors(value)]
    assert not errs, errs


@pytest.fixture
def svc4(store, validators):
    """4-eyes(기본 구성) 서비스: 검수 역할이 임상 승인을 덧붙인다. conftest의 svc는 4-eyes를 끈 구성이라 쓰지 않는다."""
    import itertools
    from conftest import Tick
    from question_authoring.wiring.service import AuthoringService
    c = itertools.count(1)
    return AuthoringService(store, clock=Tick(), new_id=lambda p: f"{p}-{next(c):04d}", require_two_person=True)


@pytest.fixture
def api(svc4):
    return Api(svc4, {"tok-a": Actor("dr-kim", "question-author"), "tok-r": Actor("dr-park", "clinical-reviewer")})


def call(api, method, url, body=None, h=H):
    return api.handle(method, url, h, body if isinstance(body, bytes) else json.dumps(body or {}).encode())


def test_every_authoring_response_shape_passes_the_contract(api):
    st, d = call(api, "POST", "/v1/drafts", {"question_set_id": "qs-new"})
    assert st == 200; ok("AuthoringResponses", "Draft", d)
    did = d["draft_id"]
    st, d = call(api, "POST", f"/v1/drafts/{did}/edits", {"edits": [{"op": "set", "path": "/questions", "value": []}]})
    assert st == 200; ok("AuthoringResponses", "Draft", d)
    st, d = call(api, "POST", f"/v1/drafts/{did}/discard", {"reason": "시험"})
    assert st == 200; ok("AuthoringResponses", "Draft", d)                                  # discarded 모양
    st, out = call(api, "POST", "/v1/materials?question_set_id=qs-main&filename=f.txt", example_text())
    assert st == 200 and out["status"] == "PARSED"; ok("AuthoringResponses", "MaterialIngested", out)
    st, d = call(api, "GET", f"/v1/drafts/{out['draft_id']}"); ok("AuthoringResponses", "Draft", d)
    st, pv = call(api, "GET", f"/v1/drafts/{out['draft_id']}/preview"); ok("AuthoringResponses", "Preview", pv)
    st, fb = call(api, "POST", "/v1/materials?question_set_id=qs-fb&filename=scan.pdf", b"%PDF-1.7 binary")
    assert st == 200 and fb["status"] == "FALLBACK"; ok("AuthoringResponses", "MaterialIngested", fb)   # 화면 입력 이어 가기 모양
    st, ref = call(api, "POST", f"/v1/drafts/{out['draft_id']}/confirm", {"preview_hash": pv["preview_hash"]})
    assert st == 200; ok("AuthoringResponses", "ConfirmResponse", ref)
    st, d = call(api, "GET", f"/v1/drafts/{out['draft_id']}"); ok("AuthoringResponses", "Draft", d)   # confirmed 모양
    st, lst = call(api, "GET", "/v1/drafts"); ok("AuthoringResponses", "DraftList", lst)
    st, rv = call(api, "POST", "/v1/reviews", {"question_set": ref, "decision": "APPROVED", "clinical_basis_approved": True}, h=REV)
    assert st == 200; ok("AuthoringResponses", "AddReviewResponse", rv)


def test_acknowledge_response_passes_the_contract(api):
    text = example_text().decode().replace("범위 34 ~ 42", "범위 34-42").encode()      # 읽지 못한 값 -> UNSURE 플래그
    st, out = call(api, "POST", "/v1/materials?question_set_id=qs-ack&filename=f.txt", text)
    assert st == 200 and out["status"] == "PARSED"
    st, d = call(api, "GET", f"/v1/drafts/{out['draft_id']}")
    flags = [f["flag_id"] for f in d["flags"]]
    assert flags
    st, d = call(api, "POST", f"/v1/drafts/{out['draft_id']}/acknowledge", {"flag_ids": flags})
    assert st == 200; ok("AuthoringResponses", "Draft", d)


def test_signals_ok_response_passes_the_contract(svc, i7):  # noqa: F811
    api = Api(svc, {"tok-a": Actor("dr-kim", "question-author")}, signals=HttpFailureStore(i7, "i7-tok"))
    st, out = call(api, "GET", "/v1/signals")
    assert st == 200 and out["status"] == "OK"
    ok("SignalsResponse", None, out)


def test_stored_ext_of_every_review_and_change_passes_the_contract(svc4):
    svc = svc4
    """저장소에서 읽은 레코드(저자 확인, 후속 검수, 변경 분류)의 ext를 계약 QuestionAuthoringExt로 본다."""
    from helpers import AUTHOR, REVIEWER, publish
    ref1 = publish(svc, text=example_text())
    ref2 = publish(svc, edits=[{"op": "set", "path": "/questions/0/name", "value": "체온 측정"}])
    svc.add_review(REVIEWER, ref2, decision="CHANGES_REQUESTED", clinical_basis_approved=False, reason="재확인")
    kinds = set()
    for ref in (ref1, ref2):
        for r in svc.store.get_reviews(ref):
            ext = r["ext"]["question-authoring"]
            ok("QuestionAuthoringExt", "ReviewExt", ext)
            kinds.add(ext["kind"])
    ch = svc.store.get_change(ref2)
    assert ch is not None
    ok("QuestionAuthoringExt", "ChangeExt", ch["ext"]["question-authoring"])
    assert {"AUTHOR_CONFIRMATION", "FOLLOW_UP_REVIEW"} <= kinds
