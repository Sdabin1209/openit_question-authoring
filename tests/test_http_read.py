"""I1 HTTP 읽기 면: 실제 서버를 띄워(포트는 OS가 배정) 표준 라이브러리 클라이언트로 읽고, 받은 레코드를 스키마와 해시로 검증한다."""
import json
import threading
import urllib.error
import urllib.request

import pytest
from boundary_contracts import canonical, validator

from helpers import stop_http
from question_authoring.http_api import Api, make_server
from question_authoring.service import Actor
from helpers import AUTHOR, publish

READ, AUTH = "read-tok", "auth-tok"


@pytest.fixture
def server(svc):
    api = Api(svc, {AUTH: Actor("dr-kim", "question-author")}, read_tokens={READ: "synthetic-data"})
    srv = make_server(api, "127.0.0.1", 0)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.effective_port}"
    stop_http(srv)


def get(base, path, token=READ):
    req = urllib.request.Request(base + path, headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def post(base, path, body, token):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_read_surface(server, svc, validators):
    v1 = publish(svc)
    v2 = publish(svc, edits=[{"op": "set", "path": "/questions/0/slots/0/units/0/range", "value": [30, 45]}])
    publish(svc, "qs-b")
    assert get(server, "/v1/question-sets") == (200, {"items": ["qs-b", "qs-main"], "next_cursor": None})
    st, page = get(server, "/v1/question-sets?limit=1")
    assert page == {"items": ["qs-b"], "next_cursor": "qs-b"}
    assert get(server, "/v1/question-sets?cursor=qs-b&limit=1")[1]["items"] == ["qs-main"]
    assert get(server, "/v1/question-sets/qs-main/versions")[1] == {"items": [v1, v2], "next_cursor": None}
    assert get(server, "/v1/question-sets/qs-main/latest")[1] == v2
    assert get(server, "/v1/question-sets/qs-main/latest?reviewed_only=true")[1] == v2
    # 레코드는 스키마와 해시를 통과한다
    st, bank = get(server, "/v1/question-sets/qs-main/versions/2")
    assert st == 200 and canonical.sha256_hash(bank) == v2["content_hash"] and bank["version"] == 2
    assert validators.errors("QuestionBank", bank) == []
    st, revs = get(server, "/v1/question-sets/qs-main/versions/2/reviews")
    assert revs["next_cursor"] is None and len(revs["items"]) == 1
    for r in revs["items"]:
        assert list(validator("ReviewRecord").iter_errors(r)) == []
        assert r["question_set"] == v2
    assert get(server, "/v1/question-sets/qs-main/versions/1/change") == (200, {"item": None})
    st, ch = get(server, "/v1/question-sets/qs-main/versions/2/change")
    assert list(validator("ChangeClassification").iter_errors(ch["item"])) == [] and ch["item"]["to"] == v2


def test_read_errors_and_auth(server, svc):
    publish(svc)
    st, e = get(server, "/v1/question-sets/none/latest")
    assert st == 404 and e["code"] == "NOT_FOUND"
    assert get(server, "/v1/question-sets/qs-main/versions/9")[0] == 404
    assert get(server, "/v1/question-sets/qs-main/latest?reviewed_only=x")[0] == 400
    assert get(server, "/v1/question-sets?limit=0")[0] == 400
    assert get(server, "/v1/question-sets?limit=501")[0] == 400
    assert get(server, "/v1/question-sets", token=None)[0] == 401
    assert get(server, "/v1/question-sets", token="wrong")[0] == 401
    # 승인 철회 후 reviewed_only
    ref = svc.store.latest("qs-main")
    svc.add_review(AUTHOR, ref, decision="REJECTED", clinical_basis_approved=False, reason="철회")
    assert get(server, "/v1/question-sets/qs-main/latest?reviewed_only=true")[0] == 404
    assert get(server, "/v1/question-sets/qs-main/latest")[0] == 200


def test_read_token_cannot_author(server):
    for path, body in (("/v1/drafts", {"question_set_id": "qs-x"}), ("/v1/reviews", {})):
        assert post(server, path, body, READ) == 403
    assert get(server, "/v1/drafts")[0] == 403
    assert post(server, "/v1/drafts", {"question_set_id": "qs-x"}, AUTH) == 200
    assert get(server, "/v1/question-sets", token=AUTH)[0] == 200      # 저작 토큰도 읽을 수 있다


def test_integrity_error_is_422(server, svc, store):
    ref = publish(svc)
    bad = store.reader().get_question_bank(ref)
    bad["questions"][0]["name"] = "변조"
    if hasattr(store, "_corrupt_for_test"):
        store._corrupt_for_test("qs-main", 1, bad)
    else:
        store._db.execute("DROP TRIGGER versions_no_update")
        store._db.execute("UPDATE versions SET bank=? WHERE qsid='qs-main'", (json.dumps(bad, ensure_ascii=False),))
    st, e = get(server, "/v1/question-sets/qs-main/versions/1")
    assert st == 422 and e["code"] == "INTEGRITY_ERROR"
