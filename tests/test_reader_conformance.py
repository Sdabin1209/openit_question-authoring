"""I1 QuestionReader 적합성 시험. 메모리, SQLite(메모리 DB), SQLite(파일)가 모두 통과해야 한다."""
import copy

import pytest
from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError
from boundary_contracts.sdk import QuestionReader

from helpers import AUTHOR, publish


def err(code, fn, *a, **k):
    with pytest.raises(BoundaryError) as e:
        fn(*a, **k)
    assert e.value.code == code, e.value
    return e.value


def edit_text(n):
    return [{"op": "set", "path": "/questions/0/question_sentence", "value": f"오늘 체온은 얼마인가요? ({n})"}]


def test_is_question_reader(store):
    assert isinstance(store.reader(), QuestionReader)
    assert isinstance(store, QuestionReader)


def test_empty_set_not_found(store):
    r = store.reader()
    err("NOT_FOUND", r.latest, "qs-none")
    err("NOT_FOUND", r.latest, "qs-none", reviewed_only=True)
    assert r.list_versions("qs-none") == ([], None)


def test_roundtrip_and_hash(store, svc, validators):
    ref = publish(svc)
    r = store.reader()
    bank = r.get_question_bank(ref)
    assert canonical.sha256_hash(bank) == ref["content_hash"]
    assert ref["version"] == "1" and bank["version"] == 1
    assert validators.errors("QuestionBank", bank) == []
    assert r.latest("qs-main") == ref
    assert r.latest("qs-main", reviewed_only=True) == ref


def test_returned_objects_are_copies(store, svc):
    ref = publish(svc)
    r = store.reader()
    b = r.get_question_bank(ref)
    b["questions"].clear()
    assert r.get_question_bank(ref)["questions"]
    rv = r.get_reviews(ref)
    rv[0]["decision"] = "REJECTED"
    assert r.get_reviews(ref)[0]["decision"] == "APPROVED"


def test_get_bank_errors(store, svc):
    ref = publish(svc)
    r = store.reader()
    err("NOT_FOUND", r.get_question_bank, {**ref, "version": "9"})
    err("NOT_FOUND", r.get_question_bank, {**ref, "id": "qs-other"})
    err("NOT_FOUND", r.get_question_bank, {**ref, "version": "abc"})
    err("INTEGRITY_ERROR", r.get_question_bank, {**ref, "content_hash": "sha256:" + "0" * 64})
    err("INVALID_RECORD", r.get_question_bank, {"id": "x"})
    err("INVALID_RECORD", r.get_question_bank, "x")


def test_versions_pagination_order(store, svc):
    refs = [publish(svc)]
    for i in range(2, 6):
        refs.append(publish(svc, edits=edit_text(i)))
    r = store.reader()
    items, nxt = r.list_versions("qs-main")
    assert items == refs and nxt is None
    got, cur = [], None
    while True:
        page, cur = r.list_versions("qs-main", cursor=cur, limit=2)
        got += page
        if cur is None:
            break
    assert got == refs
    assert r.latest("qs-main") == refs[-1]
    err("INVALID_RECORD", r.list_versions, "qs-main", limit=0)
    err("INVALID_RECORD", r.list_versions, "qs-main", cursor="zz")
    # 과거 항목의 순서는 새 항목이 붙어도 바뀌지 않는다
    publish(svc, edits=edit_text(6))
    assert r.list_versions("qs-main")[0][:5] == refs


def test_sets_are_independent(store, svc):
    a = publish(svc, "qs-a")
    b = publish(svc, "qs-b")
    r = store.reader()
    assert r.latest("qs-a") == a and r.latest("qs-b") == b
    assert a["content_hash"] != b["content_hash"] or a["id"] != b["id"]


def test_reviews_and_change(store, svc):
    v1 = publish(svc)
    v2 = publish(svc, edits=edit_text(2))
    r = store.reader()
    assert r.get_change(v1) is None
    ch = r.get_change(v2)
    assert ch["from"] == v1 and ch["to"] == v2 and ch["change_kinds"] == ["TEXT"]
    assert len(r.get_reviews(v1)) == 1
    rv = r.get_reviews(v1)[0]
    assert rv["decision"] == "APPROVED" and rv["clinical_basis_approved"] is True
    assert rv["question_set"] == v1


def test_reviewed_only_semantics(store, svc):
    v1 = publish(svc)
    v2 = publish(svc, edits=edit_text(2))
    r = store.reader()
    assert r.latest("qs-main", reviewed_only=True) == v2
    # 승인 철회: v2의 가장 최근 검수가 반려이면 reviewed_only에서 빠지고 v1이 최신이 된다
    svc.add_review(AUTHOR, v2, decision="REJECTED", clinical_basis_approved=False, reason="근거 재검토")
    assert r.latest("qs-main") == v2
    assert r.latest("qs-main", reviewed_only=True) == v1
    assert [x["decision"] for x in r.get_reviews(v2)] == ["APPROVED", "REJECTED"]
    # 승인이어도 clinical_basis_approved가 false이면 나오지 않는다
    svc.add_review(AUTHOR, v1, decision="APPROVED", clinical_basis_approved=False)
    err("NOT_FOUND", r.latest, "qs-main", reviewed_only=True)
    # 다시 승인하면 돌아온다
    svc.add_review(AUTHOR, v2, decision="APPROVED", clinical_basis_approved=True)
    assert r.latest("qs-main", reviewed_only=True) == v2


def test_tampered_content_detected(store, svc):
    ref = publish(svc)
    bad = store.reader().get_question_bank(ref)
    bad["questions"][0]["name"] = "바뀐 이름"
    if hasattr(store, "_corrupt_for_test"):
        store._corrupt_for_test("qs-main", 1, bad)
    else:  # SQLite: 불변 트리거를 일부러 떼고 내용을 바꾼다
        import json
        store._db.execute("DROP TRIGGER versions_no_update")
        store._db.execute("UPDATE versions SET bank=? WHERE qsid='qs-main'", (json.dumps(bad, ensure_ascii=False),))
    err("INTEGRITY_ERROR", store.reader().get_question_bank, ref)


def test_commit_idempotent_and_conflict(store, svc):
    ref = publish(svc)
    bank = store.reader().get_question_bank(ref)
    rv = store.reader().get_reviews(ref)[0]
    store.commit_version(copy.deepcopy(bank), ref, rv, None, None)     # 같은 내용: 성공
    assert len(store.reader().list_versions("qs-main")[0]) == 1
    other = copy.deepcopy(bank)
    other["questions"][0]["name"] = "다른 내용"
    oref = {**ref, "content_hash": canonical.sha256_hash(other)}
    err("CONFLICT", store.commit_version, other, oref, {**rv, "review_id": "rv-x", "question_set": oref}, None, None)
    gap = {**other, "version": 3}
    gref = {"id": "qs-main", "version": "3", "content_hash": canonical.sha256_hash(gap)}
    err("CONFLICT", store.commit_version, gap, gref, {**rv, "review_id": "rv-y", "question_set": gref}, None, None)
    err("INTEGRITY_ERROR", store.commit_version, bank, {**ref, "content_hash": "sha256:" + "1" * 64}, rv, None, None)
