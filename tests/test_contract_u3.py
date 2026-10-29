"""단위 3: 손으로 하던 경계 검증을 계약 스키마(D107 승격, 설치된 boundary_contracts)로 바꾼 동작 시험.
기대값 출처: 계약 패키지의 conformance/examples(통과), examples_invalid(거부)와 conformance/promoted MANIFEST의 note.
- AuthoringRequests: 모르는 키 거부(MANIFEST note: 이전 코드는 무시했다. 동작 변경)
- AuthoringResponses·SignalsResponse·ReadyzResponse: 서버가 자기 응답을 검사한다
- QuestionAuthoringExt: ReviewRecord/ChangeClassification ext를 쓰기 전에 검사한다
- QuestionAuthoringDeployInterface: 환경 변수, /readyz, purge 출력(JSON)"""
import io
import json
import pathlib

import pytest

import conformance
import boundary_contracts as bc
from question_authoring import __main__ as qa_main
from question_authoring import contract
from question_authoring.http_api import Api
from question_authoring.service import Actor
from conftest import example_text

CONF = pathlib.Path(conformance.__file__).parent
H = {"Authorization": "Bearer tok-a"}
HASH = "sha256:" + "0" * 64


def ex(kind, name):
    d = "examples" if kind == "valid" else "examples_invalid"
    return json.loads((CONF / d / f"{name}.json").read_text(encoding="utf-8"))


def defv(schema, d):
    v = bc.validator(schema)
    return v.evolve(schema={"$ref": f"#/$defs/{d}"}) if d else v


def call(api, method, url, body=None, headers=H):
    data = body if isinstance(body, bytes) else json.dumps(body or {}).encode()
    return api.handle(method, url, headers, data)


@pytest.fixture
def api(svc):
    return Api(svc, {"tok-a": Actor("dr-kim", "question-author")}, signals=None)


def draft(api):
    st, out = call(api, "POST", "/v1/materials?question_set_id=qs-main&filename=f.txt", example_text())
    assert st == 200, out
    return out["draft_id"]


# ---------- AuthoringRequests: 모르는 키·잘못된 값 거부 ----------
BODY_ROUTES = {   # def -> (method, url 만드는 법)
    "CreateDraftRequest": lambda did: "/v1/drafts",
    "EditsRequest": lambda did: f"/v1/drafts/{did}/edits",
    "AcknowledgeRequest": lambda did: f"/v1/drafts/{did}/acknowledge",
    "ConfirmRequest": lambda did: f"/v1/drafts/{did}/confirm",
    "DiscardRequest": lambda did: f"/v1/drafts/{did}/discard",
    "AddReviewRequest": lambda did: "/v1/reviews",
}


@pytest.mark.parametrize("name", sorted(BODY_ROUTES))
def test_request_bodies_invalid_examples_are_400(api, name):
    did = draft(api)
    st, out = call(api, "POST", BODY_ROUTES[name](did), ex("invalid", f"AuthoringRequests.{name}"))
    assert st == 400 and out["code"] == "INVALID_RECORD", (st, out)
    assert "AuthoringRequests" in out["message"]


@pytest.mark.parametrize("name", sorted(BODY_ROUTES))
def test_request_bodies_reject_unknown_key(api, name):
    did = draft(api)
    body = dict(ex("valid", f"AuthoringRequests.{name}"), typo_field=1)
    st, out = call(api, "POST", BODY_ROUTES[name](did), body)
    assert st == 400 and "AuthoringRequests" in out["message"], (st, out)


def test_unknown_key_does_not_reach_the_service(api, store):
    """오타 난 base_version이 조용히 무시되던 것(최신 기준 초안이 만들어짐)이 이제 거부되고 초안도 안 생긴다."""
    st, _ = call(api, "POST", "/v1/drafts", {"question_set_id": "qs-main", "base_versoin": "1"})
    assert st == 400 and api.svc.list_drafts() == []


def test_material_query_unknown_key_and_bad_flag(api):
    for q in ("question_set_id=qs-main&filename=f.txt&nope=1", "question_set_id=qs-main&filename=f.txt&accept_phi_warning=yes"):
        st, out = call(api, "POST", "/v1/materials?" + q, example_text())
        assert st == 400 and "AuthoringRequests" in out["message"], (q, st, out)


def test_signals_query_rejected(svc):
    api = Api(svc, {"tok-a": Actor("dr-kim", "question-author")}, signals=object())
    for q in ("max_cases=many", "typo=1"):
        st, out = call(api, "GET", "/v1/signals?" + q)
        assert st == 400 and "AuthoringRequests" in out["message"], (q, st, out)


def test_valid_request_examples_pass_the_request_check():
    for name in list(BODY_ROUTES) + ["MaterialQuery", "SignalsQuery"]:
        contract.check_request(name, ex("valid", f"AuthoringRequests.{name}"))


# ---------- 응답 자기 검사 ----------
def test_flow_responses_validate_against_contract(api):
    did = draft(api)
    st, d = call(api, "GET", f"/v1/drafts/{did}")
    assert not list(defv("AuthoringResponses", "Draft").iter_errors(d))
    st, pv = call(api, "GET", f"/v1/drafts/{did}/preview")
    assert not list(defv("AuthoringResponses", "Preview").iter_errors(pv))
    st, lst = call(api, "GET", "/v1/drafts")
    assert not list(defv("AuthoringResponses", "DraftList").iter_errors(lst))
    st, ref = call(api, "POST", f"/v1/drafts/{did}/confirm", {"preview_hash": pv["preview_hash"]})
    assert st == 200 and not list(defv("AuthoringResponses", "ConfirmResponse").iter_errors(ref))


def test_response_that_breaks_the_contract_is_503_not_sent(api, monkeypatch):
    did = draft(api)
    real = api.svc.get_draft
    monkeypatch.setattr(api.svc, "get_draft", lambda i: dict(real(i), leaked_internal=1))
    st, out = call(api, "GET", f"/v1/drafts/{did}")
    assert st == 503 and out["code"] == "UNAVAILABLE" and "leaked_internal" not in json.dumps(out)


def test_signals_disabled_and_broken(api, monkeypatch):
    st, out = call(api, "GET", "/v1/signals")
    assert st == 200 and not list(defv("SignalsResponse", None).iter_errors(out))
    api.signals = object()
    monkeypatch.setattr("question_authoring.signals.build_signals", lambda *a, **k: {"status": "OK", "transcript": "x"})
    st, out = call(api, "GET", "/v1/signals")
    assert st == 503 and out["code"] == "UNAVAILABLE"


# ---------- QuestionAuthoringExt ----------
def test_ext_is_checked_before_writing(svc, monkeypatch):
    seen = []
    real = contract.check_ext
    monkeypatch.setattr(contract, "check_ext", lambda d, v: (seen.append(d), real(d, v))[1])
    from helpers import AUTHOR, publish
    publish(svc, text=example_text())
    publish(svc, edits=[{"op": "set", "path": "/questions/0/name", "value": "체온 측정"}])
    assert "ReviewExt" in seen and "ChangeExt" in seen
    for r in svc.store.reader().get_reviews(svc.store.reader().latest("qs-main")):
        contract.check_ext("ReviewExt", r["ext"]["question-authoring"])


def test_ext_check_rejects_invalid_examples():
    for n, d in (("ReviewExt", "ReviewExt"), ("ChangeExt", "ChangeExt")):
        with pytest.raises(Exception):
            contract.check_ext(d, {"unknown": 1})


# ---------- QuestionAuthoringDeployInterface ----------
def test_readyz_body_is_checked(svc):
    ok = Api(svc, {}, db_kind="sqlite")
    st, out = ok.handle("GET", "/readyz", {})
    assert st == 200 and not list(defv("QuestionAuthoringDeployInterface", "ReadyzResponse").iter_errors(out))
    bad = Api(svc, {}, db_kind="mysql")
    assert bad.handle("GET", "/readyz", {})[0] == 503


@pytest.mark.parametrize("name", ["QA_DRAFT_RETENTION_DAYS", "QA_USER_ROLES"])
def test_environment_invalid_example_refuses_to_build(monkeypatch, name):
    env = ex("invalid", "QuestionAuthoringDeployInterface.Environment")
    monkeypatch.setenv("QA_DB_PATH", ":memory:")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(SystemExit) as e:
        qa_main.build()
    assert "QA_" in str(e.value)


@pytest.mark.parametrize("k,v", [("QA_PORT", "abc"), ("QA_DRAFT_RETENTION_DAYS", "0"), ("QA_REQUIRE_TWO_PERSON", "maybe"),
                                 ("QA_MAX_CONCURRENT_REQUESTS", "0")])
def test_environment_each_bad_value_refuses(monkeypatch, k, v):
    monkeypatch.setenv("QA_DB_PATH", ":memory:")
    monkeypatch.setenv(k, v)
    with pytest.raises(SystemExit) as e:
        qa_main.build()
    assert k in str(e.value)


def test_environment_valid_example_builds(monkeypatch, tmp_path):
    env = {k: v for k, v in ex("valid", "QuestionAuthoringDeployInterface.Environment").items() if not k.endswith("_FILE")}
    monkeypatch.setenv("QA_DB_PATH", str(tmp_path / "q.sqlite3"))
    monkeypatch.delenv("QA_FAILURES_URL", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("QA_FAILURES_URL", raising=False)
    api, store = qa_main.build()
    store.close()


def test_purge_prints_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("QA_DB_PATH", str(tmp_path / "q.sqlite3"))
    assert qa_main.main(["purge"]) == 0
    line = capsys.readouterr().out.strip().splitlines()[-1]
    out = json.loads(line)       # 파이썬 dict 표기(작은따옴표)면 여기서 실패한다
    assert not list(defv("QuestionAuthoringDeployInterface", "PurgeResult").iter_errors(out))


def test_purge_result_with_removals_passes_contract_purge_result(tmp_path):
    """0건이 아닌 결과도 계약 PurgeResult를 통과한다(purge 출력은 이 dict를 JSON 한 줄로 낸 것이다, __main__.main). 감사: 0건 한 경우만 검증했다."""
    from datetime import datetime, timedelta, timezone
    from helpers import AUTHOR
    from question_authoring.service import AuthoringService
    from question_authoring.store import SqliteQuestionStore
    from question_authoring.validation import Validators
    store = SqliteQuestionStore(tmp_path / "q.sqlite3")
    svc = AuthoringService(store, Validators(), require_two_person=False)
    assert svc.ingest_material(AUTHOR, "qs-purge", "f.txt", example_text())["status"] == "PARSED"
    res = qa_main.purge_once(store, now=datetime.now(timezone.utc) + timedelta(days=400))
    assert res["drafts_removed"] == 1 and res["sources_removed"] == 1
    assert not list(defv("QuestionAuthoringDeployInterface", "PurgeResult").iter_errors(json.loads(json.dumps(res))))
    store.close()
