"""구조 잠금(U81): 둘째 버전부터 문항 ID·칸 ID 구조는 기반 버전과 같아야 한다. 문장·수치는 바꿀 수 있고 첫 버전은 면제다."""
import copy

import pytest
from boundary_contracts.errors import BoundaryError

from question_authoring.components import confirm_finalize, draft_edit, draft_ingest
from conftest import example_text
from helpers import AUTHOR, publish


def _v1(svc, store):
    ref = publish(svc, "qs-lock")
    return ref, store.get_question_bank(ref)


def _draft(svc):
    return svc.create_draft(AUTHOR, "qs-lock")["draft_id"]


def _refused(svc, did, edits):
    with pytest.raises(BoundaryError) as e:
        svc.apply_edits(AUTHOR, did, edits)
    assert e.value.code == "FAILED_PRECONDITION"
    return str(e.value)


def test_first_version_is_exempt(svc, store):
    ref, bank = _v1(svc, store)
    assert ref["version"] == "1" and bank["questions"]


def test_add_question_is_refused_and_names_the_id(svc, store):
    _, bank = _v1(svc, store)
    q = copy.deepcopy(bank["questions"][0]); q["question_id"] = "새문항"
    assert "새문항" in _refused(svc, _draft(svc), [{"op": "append", "path": "/questions", "value": q}])


def test_remove_question_is_refused(svc, store):
    _, bank = _v1(svc, store)
    gone = bank["questions"][-1]["question_id"]
    assert gone in _refused(svc, _draft(svc), [{"op": "remove", "path": f"/questions/{len(bank['questions']) - 1}"}])


def test_rename_question_id_is_refused_with_both_ids(svc, store):
    _, bank = _v1(svc, store)
    old = bank["questions"][0]["question_id"]
    msg = _refused(svc, _draft(svc), [{"op": "set", "path": "/questions/0/question_id", "value": "Renamed"}])
    assert old in msg and "Renamed" in msg


def test_add_slot_is_refused(svc, store):
    _, bank = _v1(svc, store)
    s = copy.deepcopy(bank["questions"][0]["slots"][0]); s["slot_id"] = "extra_slot"
    assert "extra_slot" in _refused(svc, _draft(svc), [{"op": "append", "path": "/questions/0/slots", "value": s}])


def test_remove_slot_is_refused(svc, store):
    _, bank = _v1(svc, store)
    gone = bank["questions"][0]["slots"][0]["slot_id"]
    assert gone in _refused(svc, _draft(svc), [{"op": "remove", "path": "/questions/0/slots/0"}])


def test_refused_edit_leaves_the_draft_unchanged(svc, store):
    _, bank = _v1(svc, store)
    did = _draft(svc)
    _refused(svc, did, [{"op": "set", "path": "/questions/0/question_id", "value": "Renamed"}])
    assert svc.get_draft(did)["bank"]["questions"][0]["question_id"] == bank["questions"][0]["question_id"]


def test_sentence_only_edit_passes_and_makes_version_2(svc, store):
    _, bank = _v1(svc, store)
    did = _draft(svc)
    svc.apply_edits(AUTHOR, did, [{"op": "set", "path": "/questions/0/question_sentence", "value": "문장만 바꾼다"}])
    pv = svc.get_preview(did)
    assert pv["ready_to_confirm"]
    assert svc.confirm(AUTHOR, did, pv["preview_hash"])["version"] == "2"


def test_ingest_missing_a_question_is_refused_and_leaves_no_open_draft(svc, store):
    _v1(svc, store)
    first = example_text().decode().rsplit("\n문항 ", 1)
    assert len(first) == 2 and example_text().decode().count("\n문항 ") >= 2, "예시 자료에 문항이 둘 이상 필요하다"
    with pytest.raises(BoundaryError) as e:
        svc.ingest_material(AUTHOR, "qs-lock", "less.txt", first[0].encode())
    assert e.value.code == "FAILED_PRECONDITION"
    assert [d for d in svc.list_drafts("qs-lock") if d["status"] == "OPEN"] == []


def test_ingest_same_structure_passes(svc, store):
    _v1(svc, store)
    out = svc.ingest_material(AUTHOR, "qs-lock", "same.txt", example_text("qs-lock"))
    assert out["status"] == "PARSED"


def test_confirm_refuses_when_the_draft_was_changed_behind_the_edit_guard(svc, store):
    """3차 방어: 편집 경로를 거치지 않고 초안이 바뀌어도(저장소 직접 수정 등) 확정은 구조가 다르면 새 버전을 만들지 않는다."""
    _, bank = _v1(svc, store)
    did = _draft(svc)
    d = svc.get_draft(did)
    d["bank"]["questions"][0]["question_id"] = "Sneaky"
    svc._touch(d)
    pv = svc.get_preview(did)
    with pytest.raises(BoundaryError) as e:
        svc.confirm(AUTHOR, did, pv["preview_hash"])
    assert e.value.code == "FAILED_PRECONDITION" and "Sneaky" in str(e.value)
    assert store.latest("qs-lock")["version"] == "1"


def test_three_components_agree():
    old = {"questions": [{"question_id": "a", "slots": [{"slot_id": "x"}, {"slot_id": "y"}]}, {"question_id": "b", "slots": []}]}
    cases = [old, {"questions": []}, {"questions": [old["questions"][1], old["questions"][0]]},
             {"questions": [{"question_id": "a", "slots": [{"slot_id": "x"}]}, old["questions"][1]]},
             {"questions": [dict(old["questions"][0], question_id="c"), old["questions"][1]]}]
    for base in (None, old):
        for new in cases:
            r = {m.structure_lock_problem(base, new) for m in (draft_edit, draft_ingest, confirm_finalize)}
            assert len(r) == 1
    assert draft_edit.structure_lock_problem(old, cases[2]) is None      # 문항 목록 순서는 구조가 아니다(order 필드가 정한다)
    assert draft_edit.structure_lock_problem(None, cases[1]) is None     # 첫 버전
