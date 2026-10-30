"""I1 서버 적합성 시험: 우리 서버를 띄워 boundary_contracts.clients.HttpQuestionReader(읽기 모드 검증, 해시 재계산)로 읽는다."""
import socket
import threading

import pytest
from boundary_contracts import canonical
from boundary_contracts.clients import HttpQuestionReader
from boundary_contracts.errors import BoundaryError
from boundary_contracts.sdk import QuestionReader

from helpers import stop_http
from question_authoring.components.link_authoring_server import Api, make_server
from question_authoring.wiring.service import Actor
from helpers import AUTHOR, publish

READ = "read-tok"


@pytest.fixture
def served(svc):
    api = Api(svc, {"auth-tok": Actor("dr-kim", "question-author")}, read_tokens={READ: "synthetic-data"})
    srv = make_server(api, "127.0.0.1", 0)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{srv.effective_port}"
    yield svc, url, srv
    stop_http(srv)


def err(code, fn, *a, **k):
    with pytest.raises(BoundaryError) as e:
        fn(*a, **k)
    assert e.value.code == code, e.value
    return e.value


def edit(n):
    return [{"op": "set", "path": "/questions/0/question_sentence", "value": f"체온은 얼마인가요? ({n})"}]


def test_client_is_reader(served):
    _, url, _ = served
    assert isinstance(HttpQuestionReader(url, READ), QuestionReader)


def test_pages_latest_bank_reviews_change(served):
    svc, url, _ = served
    c = HttpQuestionReader(url, READ)
    assert c.list_ids() == ([], None)
    refs = [publish(svc)]
    for i in range(2, 6):
        refs.append(publish(svc, edits=edit(i)))
    for q in ("qs-b", "qs-c"):
        publish(svc, q)
    # list_ids 여러 쪽
    got, cur, pages = [], None, 0
    while True:
        items, cur = c.list_ids(cursor=cur, limit=2)
        got += items; pages += 1
        if cur is None:
            break
    assert got == ["qs-b", "qs-c", "qs-main"] and pages == 2
    # list_versions 여러 쪽
    got, cur, pages = [], None, 0
    while True:
        items, cur = c.list_versions("qs-main", cursor=cur, limit=2)
        got += items; pages += 1
        if cur is None:
            break
    assert got == refs and pages == 3
    # latest
    assert c.latest("qs-main") == refs[-1]
    assert c.latest("qs-main", reviewed_only=True) == refs[-1]
    svc.add_review(AUTHOR, refs[-1], decision="REJECTED", clinical_basis_approved=False, reason="철회")
    assert c.latest("qs-main") == refs[-1]
    assert c.latest("qs-main", reviewed_only=True) == refs[-2]
    err("NOT_FOUND", c.latest, "qs-none")
    # 문항 세트 본문: 클라이언트가 해시 재계산
    bank = c.get_question_bank(refs[1])
    assert canonical.sha256_hash(bank) == refs[1]["content_hash"] and bank["version"] == 2
    err("INTEGRITY_ERROR", c.get_question_bank, {**refs[1], "content_hash": "sha256:" + "0" * 64})
    err("NOT_FOUND", c.get_question_bank, {**refs[1], "version": "99"})
    # 검수·변경
    revs = c.get_reviews(refs[-1])
    assert [r["decision"] for r in revs] == ["APPROVED", "REJECTED"]
    assert all(r["question_set"] == refs[-1] for r in revs)
    assert c.get_change(refs[0]) is None
    ch = c.get_change(refs[1])
    assert ch["to"] == refs[1] and ch["from"] == refs[0] and ch["change_kinds"] == ["TEXT"]
    err("NOT_FOUND", c.get_reviews, {**refs[0], "id": "qs-none"})


def test_auth(served):
    svc, url, _ = served
    publish(svc)
    for tok in ("", "wrong"):
        err("PERMISSION_DENIED", HttpQuestionReader(url, tok).list_ids)
    assert HttpQuestionReader(url, "auth-tok").list_ids()[0] == ["qs-main"]   # 저작 토큰도 읽을 수 있다


def test_tampered_content_integrity(served, store):
    svc, url, _ = served
    ref = publish(svc)
    bad = store.reader().get_question_bank(ref)
    bad["questions"][0]["name"] = "변조"
    if hasattr(store, "_corrupt_for_test"):
        store._corrupt_for_test("qs-main", 1, bad)
    else:
        import json
        store._db.execute("DROP TRIGGER versions_no_update")
        store._db.execute("UPDATE versions SET bank=? WHERE qsid='qs-main'", (json.dumps(bad, ensure_ascii=False),))
    err("INTEGRITY_ERROR", HttpQuestionReader(url, READ).get_question_bank, ref)


def test_server_down_is_unavailable(served):
    svc, url, srv = served
    publish(svc)
    c = HttpQuestionReader(url, READ, timeout=2)
    assert c.list_ids()[0] == ["qs-main"]
    stop_http(srv)       # 돌아온 뒤에는 아무도 듣지 않는다(서버 루프가 끝나기를 기다린다): 닫는 중에 연결을 받는 경쟁이 없다
    e = err("UNAVAILABLE", c.list_ids)
    assert e.retryable
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()   # 아무도 듣지 않는 포트
    err("UNAVAILABLE", HttpQuestionReader(f"http://127.0.0.1:{port}", READ, timeout=2).latest, "qs-main")


def test_client_reads_id_with_colon(served):
    """QA-13: Id 문법(CONVENTIONS 10절)은 ':'를 허용하고, 공식 클라이언트는 경로 조각을 quote(safe="")로 보낸다(clients/_http.py _q, RFC 3986).
    따라서 서버는 경로 조각을 디코딩해야 한다. 기대값은 계약 클라이언트와 계약 스키마 검증기에서 나온다."""
    svc, url, _ = served
    ref = publish(svc, "qs:a")
    c = HttpQuestionReader(url, READ)
    assert "qs:a" in c.list_ids()[0]
    assert c.latest("qs:a", reviewed_only=False) == ref
    assert c.list_versions("qs:a") == ([ref], None)
    assert c.get_question_bank(ref)["version"] == int(ref["version"])
    assert c.get_reviews(ref)
    assert c.get_change(ref) is None   # 첫 버전
