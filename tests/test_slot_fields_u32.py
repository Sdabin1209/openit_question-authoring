"""U32(D115): 작성 양식이 칸 규격(자료형·값 단위·최소·최대·초과 최소)을 읽어 문항 은행 SlotDef에 넣는다.
기대값 출처: 문진 스키마 survey-schema-v1.2.0의 SlotDef 선택 속성 5개와 계약 QuestionFieldSpec(boundary-contracts v2.4.1).
어느 칸이 0~7인지 같은 값은 이 영역이 정하지 않는다. 아래 값은 문법 시연용 예시이고 학습 데이터셋 기준값(survey-service
tests/fixtures_question_fields.json)과 같은 모양으로 손으로 적었다."""
import boundary_contracts as bc
import pytest

from conftest import example_text
from question_authoring.components import change_diff as classify, preview_build as preview
from question_authoring.components.extract_textform import TextFormExtractor

X = TextFormExtractor()
HEAD = """세션 설정
위험 문구: 없음
확인 한도: 3
인식 실패 연속 한도: 3
질문 횟수 한도: 30
문항 Q10: 운동
순서: 1
선행 조건: 없음
질문: 일주일에 며칠 운동하나요?
거절 설명: 필요함
정책: 질문 한도 3, 모름 2, 거절 2
"""
SLOT = """슬롯 days: 필수
다시 묻기: 며칠인지 숫자로
{extra}단위: 일 | 범위 0 ~ 7
예아니오: 없음
위험 기준: 없음
"""


def extract(extra):
    r = X.extract((HEAD + SLOT.format(extra=extra)).encode(), "f.txt")
    return r, {(f.path, f.kind) for f in r.flags}


SP = "/questions/0/slots/0/"


def test_reads_all_five_attributes(validators):
    r, f = extract("자료형: integer\n값 단위: day/week\n최소: 0\n최대: 7\n초과 최소: -1\n")
    s = r.bank["questions"][0]["slots"][0]
    assert (s["data_type"], s["unit"], s["minimum"], s["maximum"], s["exclusive_minimum"]) == ("integer", "day/week", 0, 7, -1)
    assert f == set()
    assert validators.errors("QuestionBank", dict(r.bank, version=1)) == []


def test_slot_without_attributes_is_unchanged_and_not_flagged():
    r, f = extract("")
    s = r.bank["questions"][0]["slots"][0]
    assert not {"data_type", "unit", "minimum", "maximum", "exclusive_minimum"} & set(s)
    assert f == set()


@pytest.mark.parametrize("line,key", [
    ("자료형: float\n", "data_type"), ("자료형:\n", "data_type"), ("값 단위:\n", "unit"),
    ("최소: abc\n", "minimum"), ("최대: 1e3\n", "maximum"), ("초과 최소: 1,5\n", "exclusive_minimum")])
def test_unreadable_value_is_flagged_not_guessed(line, key):
    r, f = extract(line)
    assert (SP + key, "UNSURE") in f
    assert key not in r.bank["questions"][0]["slots"][0]


def test_maximum_below_minimum_is_not_guessed():
    r, f = extract("최소: 5\n최대: 2\n")
    s = r.bank["questions"][0]["slots"][0]
    assert s["minimum"] == 5 and "maximum" not in s and (SP + "maximum", "UNSURE") in f


def test_duplicate_line_is_flagged_and_first_value_kept():
    r, f = extract("최소: 1\n최소: 2\n")
    assert r.bank["questions"][0]["slots"][0]["minimum"] == 1 and (SP + "minimum", "UNSURE") in f


def test_values_pass_the_contract_question_field_spec():
    """survey-service가 SlotDef에서 만드는 QuestionFieldSpec(slot=문항.칸)과 같은 모양이 계약 스키마를 통과한다."""
    r, _ = extract("자료형: integer\n값 단위: day/week\n최소: 0\n최대: 7\n")
    q = r.bank["questions"][0]
    fields = []
    for s in q["slots"]:
        if "data_type" in s:
            f = {"slot": f"{q['question_id']}.{s['slot_id']}", "data_type": s["data_type"]}
            f.update({k: s[k] for k in ("unit", "minimum", "maximum", "exclusive_minimum") if k in s})
            fields.append(f)
    assert fields and not list(bc.validator("QuestionFieldSpec").iter_errors({"fields": fields}))


def test_preview_shows_values_and_marks_unsure():
    from question_authoring.components.preview_build import render_text
    r, _ = extract("자료형: integer\n값 단위: day/week\n최소: 0\n최대: abc\n")
    draft = {"question_set_id": "qs-x", "draft_id": "dr-1", "base": None, "source": None, "bank": r.bank,
             "flags": [dict(flag_id=f"fl-{i}", path=f.path, kind=f.kind, message=f.message, raw=f.raw, resolved=False)
                       for i, f in enumerate(r.flags)], "warnings": [], "fallback": None}
    text = render_text(draft)
    assert "수치(칸): 자료형 integer, 값 단위 day/week, 최소 0" in text
    assert "최대 (비어 있음) [확인 필요:" in text


def test_changing_a_range_value_is_a_clinical_review_change():
    old = {"questions": [{"question_id": "Q10", "slots": [{"slot_id": "days", "maximum": 7}]}]}
    new = {"questions": [{"question_id": "Q10", "slots": [{"slot_id": "days", "maximum": 8}]}]}
    kinds, slots, clinical = classify.diff_banks(old, new)
    assert classify.CHOICE_UNIT in kinds and clinical and slots == {"Q10.days"}


def test_end_to_end_registers_a_version_with_the_attributes(svc):
    from helpers import AUTHOR
    text = example_text().decode().replace("단위: 도 | 같은 말: 도씨, 섭씨 | 범위 34 ~ 42",
                                           "자료형: number\n값 단위: 도\n최소: 34\n최대: 42\n단위: 도 | 같은 말: 도씨, 섭씨 | 범위 34 ~ 42")
    out = svc.ingest_material(AUTHOR, "qs-main", "form.txt", text.encode())
    assert out["status"] == "PARSED"
    pv = svc.get_preview(out["draft_id"])
    assert pv["ready_to_confirm"] and "수치(칸): 자료형 number, 값 단위 도, 최소 34, 최대 42" in pv["text"]
    ref = svc.confirm(AUTHOR, out["draft_id"], pv["preview_hash"])
    slot = svc.store.get_question_bank(ref)["questions"][0]["slots"][0]
    assert (slot["data_type"], slot["unit"], slot["minimum"], slot["maximum"]) == ("number", "도", 34, 42)
