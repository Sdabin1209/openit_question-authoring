import json

from question_authoring.components.link_authoring_server import Api
from question_authoring.wiring.service import Actor
from conftest import example_text

H = {"Authorization": "Bearer tok-a"}


def call(api, method, url, body=None, headers=H):
    data = body if isinstance(body, bytes) else json.dumps(body or {}).encode()
    return api.handle(method, url, headers, data)


def test_health_and_auth(svc):
    api = Api(svc, {"tok-a": Actor("dr-kim", "question-author")})
    assert api.handle("GET", "/healthz", {})[0] == 200
    assert api.handle("GET", "/readyz", {})[0] == 200
    st, body = api.handle("GET", "/v1/drafts", {})
    assert st == 401 and body["code"] == "PERMISSION_DENIED" and body["retryable"] is False
    assert call(api, "GET", "/v1/nope")[0] == 404


def test_flow_over_http(svc, store):
    api = Api(svc, {"tok-a": Actor("dr-kim", "question-author")})
    st, out = call(api, "POST", "/v1/materials?question_set_id=qs-main&filename=f.txt", example_text())
    assert st == 200 and out["status"] == "PARSED"
    did = out["draft_id"]
    st, pv = call(api, "GET", f"/v1/drafts/{did}/preview")
    assert st == 200 and pv["ready_to_confirm"]
    assert call(api, "POST", f"/v1/drafts/{did}/confirm", {"preview_hash": "sha256:" + "0" * 64})[0] == 412
    st, ref = call(api, "POST", f"/v1/drafts/{did}/confirm", {"preview_hash": pv["preview_hash"]})
    assert st == 200 and store.reader().latest("qs-main", reviewed_only=True) == ref
    assert call(api, "POST", "/v1/drafts", {})[0] == 400
    st, rv = call(api, "POST", "/v1/reviews", {"question_set": ref, "decision": "REJECTED",
                                               "clinical_basis_approved": False, "reason": "철회"})
    assert st == 200 and store.reader().latest("qs-main") == ref
