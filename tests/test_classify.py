import copy
import json

import pytest

from question_authoring import classify
import survey_schema
from question_authoring.extractors import TextFormExtractor
from conftest import example_text


@pytest.fixture
def bank():
    b = TextFormExtractor().extract(example_text(), "f.txt").bank
    b["version"] = 1
    return b


def d(old, mut):
    new = copy.deepcopy(old)
    mut(new)
    return classify.diff_banks(old, new)


def test_text_only(bank):
    k, s, c = d(bank, lambda b: b["questions"][0].update(question_sentence="오늘 체온을 말씀해 주세요."))
    assert k == {"TEXT"} and s == {"Q1.body_temp"} and not c


def test_choice_unit_range_flags_clinical(bank):
    k, s, c = d(bank, lambda b: b["questions"][0]["slots"][0]["units"][0].update(range=[30, 45]))
    assert k == {"CHOICE_UNIT"} and s == {"Q1.body_temp"} and c


def test_slot_add_and_risk(bank):
    def m(b):
        sl = copy.deepcopy(b["questions"][1]["slots"][0]); sl["slot_id"] = "cough_days"
        b["questions"][1]["slots"].append(sl)
    k, s, c = d(bank, m)
    assert k == {"SLOT"} and s == {"Q2.cough_days"}
    k, s, c = d(bank, lambda b: b["questions"][0]["slots"][0].update(risk_rule=None))
    assert k == {"SLOT"} and c


def test_branch(bank):
    k, s, c = d(bank, lambda b: b["questions"][1].update(prerequisite=None))
    assert k == {"BRANCH"} and s == {"Q2.cough"}
    k, s, c = d(bank, lambda b: b["questions"][0]["policy"].update(question_limit=5))
    assert k == {"BRANCH"}
    k, s, c = d(bank, lambda b: b["questions"].reverse())
    assert "BRANCH" in k


def test_question_removed(bank):
    k, s, c = d(bank, lambda b: b["questions"].pop())
    assert k == {"SLOT", "BRANCH"} and s == {"Q2.cough"}


def test_session_settings(bank):
    k, s, c = d(bank, lambda b: b["session_settings"].update(risk_phrases=["다른 문구"]))
    assert k == {"TEXT"} and c and s == set()
    k, s, c = d(bank, lambda b: b["session_settings"]["guide_list"][0].update(sentence="다른 문장 {n}"))
    assert k == {"TEXT"}
    k, s, c = d(bank, lambda b: b["session_settings"]["guide_list"][0].update(per_question=True))
    assert k == {"BRANCH"}


def test_version_field_ignored(bank):
    assert d(bank, lambda b: b.update(version=2))[0] == set()


def test_classification_record(bank, validators):
    new = copy.deepcopy(bank); new["version"] = 2
    new["questions"][0]["slots"][0]["units"][0]["range"] = [30, 45]
    to = {"id": "qs", "version": "2", "content_hash": "sha256:" + "a" * 64}
    fr = {"id": "qs", "version": "1", "content_hash": "sha256:" + "b" * 64}
    rec = classify.classify_change(bank, new, to_ref=to, from_ref=fr, created_at="2026-10-15T00:00:00Z",
                                   pipeline_schema_version="1.0")
    validators.check("ChangeClassification", rec)
    assert rec["follow_ups"] == ["REGENERATE_SAMPLES", "REEVALUATE_MODEL", "CLINICAL_REVIEW"]
    assert classify.classify_change(None, new, to_ref=to, from_ref=None, created_at="x", pipeline_schema_version="1.0") is None
    with pytest.raises(ValueError):
        classify.classify_change(bank, bank, to_ref=to, from_ref=fr, created_at="x", pipeline_schema_version="1.0")


def test_tables_cover_schema_fields():
    """QuestionBank 스키마의 필드가 분류 표에 모두 있어야 한다. 새 필드가 생기면 이 시험이 알려 준다."""
    defs = survey_schema.load_schema("QuestionBank", 1)["$defs"]
    assert set(defs["QuestionDef"]["properties"]) - {"question_id", "slots"} == set(classify.QUESTION_FIELDS)
    assert set(defs["SlotDef"]["properties"]) - {"slot_id"} == set(classify.SLOT_FIELDS)
    assert set(defs["SessionSettings"]["properties"]) == set(classify.SESSION_FIELDS)
    assert set(defs["QuestionBank"]["properties"]) == {"version", "questions", "session_settings"}
