"""브리프 4절 수용 기준을 확인하는 시험."""
import copy
import tempfile
from pathlib import Path

import pytest
from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError

from question_authoring.wiring.service import Actor, AuthoringService
from conftest import example_text
from helpers import AUTHOR, publish

BAD_RISK = example_text().decode().replace("위험 기준: >= 40 도 즉시", "위험 기준: 40도 넘으면").encode()


def code(fn, *a, **k):
    with pytest.raises(BoundaryError) as e:
        fn(*a, **k)
    return e.value


def test_draft_is_not_a_version(svc, store):
    out = svc.ingest_material(AUTHOR, "qs-main", "f.txt", example_text())
    assert code(store.reader().latest, "qs-main").code == "NOT_FOUND"
    assert store.reader().list_versions("qs-main") == ([], None)
    pv = svc.get_preview(out["draft_id"])
    assert "체온" in pv["text"] and "{" not in pv["text"].split("세션 설정")[0]   # JSON이 아니라 읽는 글
    assert code(store.reader().latest, "qs-main").code == "NOT_FOUND"


def test_confirm_makes_version_with_review_and_source(svc, store):
    out = svc.ingest_material(AUTHOR, "qs-main", "f.txt", example_text())
    pv = svc.get_preview(out["draft_id"])
    ref = svc.confirm(AUTHOR, out["draft_id"], pv["preview_hash"])
    rv = store.reader().get_reviews(ref)
    assert len(rv) == 1
    r = rv[0]
    assert (r["reviewer_role"], r["decision"], r["clinical_basis_approved"]) == ("question-author", "APPROVED", True)
    assert r["question_set"] == ref
    # 원본 해시가 출처로 남는다(canonical.file_hash와 같다)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "x"; p.write_bytes(example_text())
        want = canonical.file_hash(p)
    src = r["ext"]["question-authoring"]["source"]
    assert src["source_hash"] == want and src["extractor"] == "text-form"
    assert store.get_source(want)[1] == example_text()


def test_screen_input_source_recorded(svc, store):
    ref = publish(svc)
    d = svc.create_draft(AUTHOR, "qs-main")
    svc.apply_edits(AUTHOR, d["draft_id"], [{"op": "set", "path": "/questions/0/name", "value": "체온 확인"}])
    ref2 = svc.confirm(AUTHOR, d["draft_id"], svc.get_preview(d["draft_id"])["preview_hash"])
    assert store.reader().get_reviews(ref2)[0]["ext"]["question-authoring"]["source"] == {"kind": "screen"}


def test_open_flags_block_confirm_until_author_resolves(svc, store):
    out = svc.ingest_material(AUTHOR, "qs-main", "f.txt", BAD_RISK)
    did = out["draft_id"]
    pv = svc.get_preview(did)
    assert not pv["ready_to_confirm"] and len(pv["open_flags"]) == 1
    assert "[확인 필요" in pv["text"] and "40도 넘으면" in pv["text"]        # 눈에 띄게, 원문 그대로
    assert "수치(위험 기준): (비어 있음)" in pv["text"]                      # 추정해 채우지 않음
    e = code(svc.confirm, AUTHOR, did, pv["preview_hash"])
    assert e.code == "FAILED_PRECONDITION"
    # 저자가 직접 값을 입력하면 닫힌다
    svc.apply_edits(AUTHOR, did, [{"op": "set", "path": "/questions/0/slots/0/risk_rule",
                                   "value": {"operator": ">=", "value": {"amount": 40, "unit": "도"}, "response": "IMMEDIATE"}}])
    pv2 = svc.get_preview(did)
    assert pv2["ready_to_confirm"] and pv2["preview_hash"] != pv["preview_hash"]
    assert code(svc.confirm, AUTHOR, did, pv["preview_hash"]).code == "FAILED_PRECONDITION"  # 옛 미리보기로는 안 됨
    ref = svc.confirm(AUTHOR, did, pv2["preview_hash"])
    r = store.reader().get_reviews(ref)[0]["ext"]["question-authoring"]
    assert len(r["edited_flags"]) == 1


def test_acknowledge_flag(svc):
    text = example_text().decode().replace("위험 기준: >= 40 도 즉시", "위험 기준: >= 40 도씨 즉시").encode()
    out = svc.ingest_material(AUTHOR, "qs-main", "f.txt", text)
    pv = svc.get_preview(out["draft_id"])
    assert len(pv["open_flags"]) == 1
    svc.acknowledge_flags(AUTHOR, out["draft_id"], [pv["open_flags"][0]["flag_id"]])
    pv = svc.get_preview(out["draft_id"])
    assert pv["ready_to_confirm"] and "저자 확인함" in pv["text"]


def test_confirm_idempotent_and_hash_stable(svc, store):
    out = svc.ingest_material(AUTHOR, "qs-main", "f.txt", example_text())
    ph = svc.get_preview(out["draft_id"])["preview_hash"]
    a = svc.confirm(AUTHOR, out["draft_id"], ph)
    b = svc.confirm(AUTHOR, out["draft_id"], ph)
    assert a == b and len(store.reader().list_versions("qs-main")[0]) == 1
    assert code(svc.confirm, AUTHOR, out["draft_id"], "sha256:" + "0" * 64).code == "CONFLICT"
    assert code(svc.apply_edits, AUTHOR, out["draft_id"], []).code == "FAILED_PRECONDITION"


def test_confirm_retry_after_crash(validators):
    from question_authoring.components.question_store import QuestionStore
    from question_authoring.components.store_memory import MemoryQuestionStore
    from question_authoring.wiring import schemas

    class Flaky(MemoryQuestionStore):
        boom = False
        def save_draft(self, draft):
            if self.boom and draft.get("status") == "CONFIRMED":
                self.boom = False
                raise BoundaryError("UNAVAILABLE", "일시 장애")
            super().save_draft(draft)

    s = QuestionStore(Flaky(), schemas.check)
    svc = AuthoringService(s)
    out = svc.ingest_material(AUTHOR, "qs-main", "f.txt", example_text())
    ph = svc.get_preview(out["draft_id"])["preview_hash"]
    s._b.boom = True
    assert code(svc.confirm, AUTHOR, out["draft_id"], ph).retryable
    ref = svc.confirm(AUTHOR, out["draft_id"], ph)             # 재시도: 같은 버전, 중복 없음
    assert ref["version"] == "1" and len(s.list_versions("qs-main")[0]) == 1
    assert len(s.get_reviews(ref)) == 1


def test_stale_base_and_no_change(svc):
    publish(svc)
    d1 = svc.create_draft(AUTHOR, "qs-main")
    d2 = svc.create_draft(AUTHOR, "qs-main")
    for d, nm in ((d1, "A"), (d2, "B")):
        svc.apply_edits(AUTHOR, d["draft_id"], [{"op": "set", "path": "/questions/0/name", "value": nm}])
    svc.confirm(AUTHOR, d1["draft_id"], svc.get_preview(d1["draft_id"])["preview_hash"])
    e = code(svc.confirm, AUTHOR, d2["draft_id"], svc.get_preview(d2["draft_id"])["preview_hash"])
    assert e.code == "FAILED_PRECONDITION"
    d3 = svc.create_draft(AUTHOR, "qs-main")                     # 변경 없는 초안
    pv = svc.get_preview(d3["draft_id"])
    assert not pv["ready_to_confirm"]
    assert code(svc.confirm, AUTHOR, d3["draft_id"], pv["preview_hash"]).code == "FAILED_PRECONDITION"


def test_second_version_has_change_classification(svc, store):
    v1 = publish(svc)
    d = svc.create_draft(AUTHOR, "qs-main")
    svc.apply_edits(AUTHOR, d["draft_id"], [{"op": "set", "path": "/questions/0/slots/0/units/0/range", "value": [30, 45]}])
    pv = svc.get_preview(d["draft_id"])
    assert pv["change"]["change_kinds"] == ["CHOICE_UNIT"] and "임상 검토" in pv["change"]["text"]
    v2 = svc.confirm(AUTHOR, d["draft_id"], pv["preview_hash"])
    ch = store.reader().get_change(v2)
    assert ch["from"] == v1 and ch["affected_slots"] == ["Q1.body_temp"] and "CLINICAL_REVIEW" in ch["follow_ups"]
    assert v2["version"] == "2" and store.reader().get_question_bank(v2)["version"] == 2
    # 옛 버전은 그대로
    assert store.reader().get_question_bank(v1)["questions"][0]["slots"][0]["units"][0]["range"] == [34, 42]


def test_invalid_bank_never_stored(svc, store):
    d = svc.create_draft(AUTHOR, "qs-new", base_version=None)
    svc.apply_edits(AUTHOR, d["draft_id"], [{"op": "append", "path": "/questions", "value": {"question_id": "Q1"}}])
    pv = svc.get_preview(d["draft_id"])
    assert pv["validation_errors"] and not pv["ready_to_confirm"]
    assert code(svc.confirm, AUTHOR, d["draft_id"], pv["preview_hash"]).code == "INVALID_RECORD"
    assert store.list_versions("qs-new") == ([], None)


def test_semantic_reference_errors(svc):
    out = svc.ingest_material(AUTHOR, "qs-main", "f.txt", example_text().decode().replace("Q1.body_temp = high", "Q9.x = high").encode())
    pv = svc.get_preview(out["draft_id"])
    assert any("없는 문항" in e for e in pv["validation_errors"])


def test_fallback_to_screen_input(svc, store):
    out = svc.ingest_material(AUTHOR, "qs-main", "scan.pdf", b"%PDF-1.7 binary")
    assert out["status"] == "FALLBACK"
    d = svc.get_draft(out["draft_id"])
    assert d["fallback"] and d["source"]["source_hash"].startswith("sha256:")
    pv = svc.get_preview(out["draft_id"])
    assert not pv["ready_to_confirm"] and pv["fallback"]
    # 화면 입력으로 같은 초안을 채워 같은 절차로 등록
    bank = TextBank()
    svc.apply_edits(AUTHOR, out["draft_id"], [{"op": "set", "path": "/questions", "value": bank["questions"]},
                                              {"op": "set", "path": "/session_settings", "value": bank["session_settings"]}])
    pv = svc.get_preview(out["draft_id"])
    ref = svc.confirm(AUTHOR, out["draft_id"], pv["preview_hash"])
    src = store.reader().get_reviews(ref)[0]["ext"]["question-authoring"]["source"]
    assert src["source_hash"] == d["source"]["source_hash"]                # 실패한 원본도 출처로 남는다


def TextBank():
    from question_authoring.components.extract_textform import TextFormExtractor
    return TextFormExtractor().extract(example_text(), "f.txt").bank


def test_phi_warning(svc, store):
    text = example_text() + "\n# 환자 900101-1234567\n".encode()
    e = code(svc.ingest_material, AUTHOR, "qs-main", "f.txt", text)
    assert e.code == "FAILED_PRECONDITION" and e.details["warnings"] == ["주민등록번호 형태"]
    assert svc.list_drafts() == []
    assert svc.ingest_material(AUTHOR, "qs-main", "f.txt", text, accept_phi_warning=True)["warnings"]


def test_permissions(svc):
    for actor in (None, Actor("x", "visitor")):
        assert code(svc.create_draft, actor, "qs-main").code == "PERMISSION_DENIED"
    out = svc.ingest_material(AUTHOR, "qs-main", "f.txt", example_text())
    ph = svc.get_preview(out["draft_id"])["preview_hash"]
    assert code(svc.confirm, Actor("x", "visitor"), out["draft_id"], ph).code == "PERMISSION_DENIED"
    assert code(svc.create_draft, AUTHOR, "bad id!").code == "INVALID_RECORD"


def test_discard(svc):
    out = svc.ingest_material(AUTHOR, "qs-main", "f.txt", example_text())
    svc.discard_draft(AUTHOR, out["draft_id"], "잘못 올림")
    assert code(svc.confirm, AUTHOR, out["draft_id"], "sha256:" + "0" * 64).code == "FAILED_PRECONDITION"


def test_add_review_idempotent(svc, store):
    v = publish(svc)
    a = svc.add_review(AUTHOR, v, decision="CHANGES_REQUESTED", clinical_basis_approved=False, reason="재확인", review_id="rv-ext1")
    b = svc.add_review(AUTHOR, v, decision="CHANGES_REQUESTED", clinical_basis_approved=False, reason="재확인", review_id="rv-ext1")
    assert a == b and len(store.get_reviews(v)) == 2
    assert code(svc.add_review, AUTHOR, v, decision="APPROVED", clinical_basis_approved=True, review_id="rv-ext1").code == "CONFLICT"
    assert code(svc.add_review, AUTHOR, v, decision="REJECTED", clinical_basis_approved=False).code == "INVALID_RECORD"  # 사유 필수


def test_edit_paths(svc):
    d = svc.create_draft(AUTHOR, "qs-new", base_version=None)
    assert code(svc.apply_edits, AUTHOR, d["draft_id"], [{"op": "remove", "path": "/questions/3"}]).code == "INVALID_RECORD"
    assert code(svc.apply_edits, AUTHOR, d["draft_id"], [{"op": "zap", "path": "/a"}]).code == "INVALID_RECORD"
    # 실패한 편집은 초안에 반영되지 않는다
    assert svc.get_draft(d["draft_id"])["bank"] == {"questions": []}
