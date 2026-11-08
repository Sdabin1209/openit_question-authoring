"""문진표 ID·제목(필수)과 선행 조건 6연산자(ADR 0027), 운영 문진표 양식 둘의 추출과 검증."""
import base64
from pathlib import Path

import pytest
from boundary_contracts.errors import BoundaryError

from conftest import example_text
from helpers import AUTHOR, publish
from question_authoring.components import bank_validate, change_diff, extract_textform
from question_authoring.components.extract_textform import TextFormExtractor

FORMS = Path(__file__).resolve().parent.parent / "forms"
OPS = ["==", "!=", ">", ">=", "<", "<="]


def form(pre, extra_slot=""):
    return (f"문진표 ID: qs-p\n문진표 제목: 시험\n세션 설정\n위험 문구: 없음\n확인 한도: 3\n인식 실패 연속 한도: 3\n질문 횟수 한도: 3\n"
            + "".join(f"고정 문장 {k}: 문장\n" for k in ("ASK_REPEAT", "FAREWELL", "RISK_NOTICE", "CALL_STAFF", "RESUME", "SURVEY_STOPPED")) + "\n"
            
            f"문항 Q1: 가\n순서: 1\n선행 조건: 없음\n질문: 가?\n거절 설명: x\n정책: 질문 한도 3, 모름 2, 거절 2\n"
            f"슬롯 yn: 필수\n자료형: boolean\n다시 묻기: x\n예아니오: 없음\n위험 기준: 없음\n"
            f"슬롯 days: 필수\n자료형: integer\n다시 묻기: x\n예아니오: 없음\n위험 기준: 없음\n"
            f"슬롯 kind: 필수\n자료형: option\n선택지: a, b\n다시 묻기: x\n예아니오: 없음\n위험 기준: 없음\n\n"
            f"문항 Q2: 나\n순서: 2\n선행 조건: {pre}\n질문: 나?\n거절 설명: x\n정책: 질문 한도 3, 모름 2, 거절 2\n"
            f"슬롯 s: 필수\n자료형: boolean\n다시 묻기: x\n예아니오: 없음\n위험 기준: 없음\n")


def pre_of(pre):
    r = TextFormExtractor().extract(form(pre).encode(), "f.txt")
    return r, r.bank["questions"][1].get("prerequisite", "UNSET")


@pytest.mark.parametrize("op", OPS)
def test_six_operators_parse_with_number_value(op):
    r, p = pre_of(f"Q1.days {op} 3")
    assert p == {"slot": {"question_id": "Q1", "repeat_key": "", "slot_id": "days"}, "op": op, "value": 3} and not r.flags
    assert isinstance(p["value"], int) and not isinstance(p["value"], bool)


def test_value_conversion_follows_the_slot_kind():
    assert pre_of("Q1.yn == true")[1]["value"] is True
    assert pre_of("Q1.yn != false")[1]["value"] is False
    assert pre_of("Q1.days >= 2.5")[1]["value"] == 2.5
    assert pre_of("Q1.kind == a")[1]["value"] == "a"


@pytest.mark.parametrize("pre,slot", [("Q1.yn == maybe", "yn"), ("Q1.days == many", "days")])
def test_unconvertible_value_is_flagged_not_guessed(pre, slot):
    r, p = pre_of(pre)
    assert p == "UNSET" and ("/questions/1/prerequisite", "UNSURE") in {(f.path, f.kind) for f in r.flags}


@pytest.mark.parametrize("pre", ["Q1.days = 3", "Q1.days <> 3", "Q1.days 3", "Q1.days ==", "Q1 == 3", "Q1.days == 3 4"])
def test_bad_forms_are_flagged(pre):
    r, p = pre_of(pre)
    assert p == "UNSET" and any(f.kind == "UNSURE" for f in r.flags)


def bank_with(op, value, slot="days"):
    r = TextFormExtractor().extract(form("없음").encode(), "f.txt")
    b = r.bank
    b["version"] = 1
    b["questions"][1]["prerequisite"] = {"slot": {"question_id": "Q1", "repeat_key": "", "slot_id": slot}, "op": op, "value": value}
    return b


def errs(b):
    return bank_validate.run({"bank": b, "version": None, "question_set_id": "qs-p"})["errors"]


def test_validate_checks_op_value_kind_and_slot():
    assert errs(bank_with(">=", 3)) == []
    assert errs(bank_with("~", 3))                                  # 없는 연산자
    assert errs(bank_with("==", "3"))                               # 수 칸에 문자열
    assert errs(bank_with("==", True))                              # 수 칸에 bool
    assert errs(bank_with(">", True, "yn"))                         # bool 칸에 순서 연산자
    assert errs(bank_with("==", 1, "yn"))                           # bool 칸에 수
    assert errs(bank_with(">", "a", "kind"))                        # 문자열 칸에 순서 연산자
    assert errs(bank_with("==", 3, "nope"))                         # 없는 칸


def test_slot_kind_is_the_same_in_extractor_and_validator():
    for s in ({"yes_no_map": {"yes": "y", "no": "n"}}, {"data_type": "boolean"}, {"data_type": "integer"}, {"units": [{"name": "도"}]}, {"data_type": "option"}, {}):
        assert extract_textform.slot_kind(s) == bank_validate.slot_kind(s)


def test_missing_id_and_title_are_refused():
    text = form("없음").replace("문진표 ID: qs-p\n", "").replace("문진표 제목: 시험\n", "")
    r = TextFormExtractor().extract(text.encode(), "f.txt")
    assert {("/question_set_id", "MISSING"), ("/title", "MISSING")} <= {(f.path, f.kind) for f in r.flags}
    b = dict(r.bank, version=1)
    e = errs(b)
    assert any("question_set_id" in x for x in e) and any("title" in x for x in e)
    assert bank_validate.run({"bank": dict(b, question_set_id="qs-p", title=""), "version": None, "question_set_id": "qs-p"})["errors"]


def test_service_refuses_form_without_id_and_title(svc):
    text = form("없음").replace("문진표 ID: qs-p\n", "").replace("문진표 제목: 시험\n", "").encode()
    out = svc.ingest_material(AUTHOR, "qs-p", "f.txt", text)
    pv = svc.get_preview(out["draft_id"])
    assert not pv["ready_to_confirm"] and any("question_set_id" in e for e in pv["validation_errors"])
    with pytest.raises(BoundaryError):
        svc.confirm(AUTHOR, out["draft_id"], pv["preview_hash"])


def test_id_must_equal_upload_id(svc):
    with pytest.raises(BoundaryError) as e:
        svc.ingest_material(AUTHOR, "qs-other", "f.txt", form("없음").encode())
    assert e.value.code == "FAILED_PRECONDITION"
    assert svc.list_drafts("qs-other") == [] or all(d["status"] != "OPEN" for d in svc.list_drafts("qs-other"))


def test_i1_response_carries_id_and_title(svc, store):
    ref = publish(svc, "qs-p", text=form("Q1.days != 0").encode())
    bank = store.reader().get_question_bank(ref)
    assert bank["question_set_id"] == "qs-p" and bank["title"] == "시험"
    assert bank["questions"][1]["prerequisite"]["op"] == "!="


def test_preview_and_diff_show_op_and_title(svc):
    out = svc.ingest_material(AUTHOR, "qs-p", "f.txt", form("Q1.days >= 2").encode())
    t = svc.get_preview(out["draft_id"])["text"]
    assert "문진표 ID: qs-p" in t and "문진표 제목: 시험" in t and ">= 2" in t
    a = bank_with("==", 3)
    b = bank_with("!=", 3)
    assert "BRANCH" in change_diff.run({"old": a, "new": b})["change_kinds"]
    c = dict(a, title="다른 제목")
    assert change_diff.run({"old": a, "new": c})["change_kinds"] == ["TEXT"]


FORM_FILES = ["health-screening-v1.txt", "health-screening-short-order.txt"]


@pytest.mark.parametrize("name,qsid,title", [(FORM_FILES[0], "health-screening", "건강검진 문진표"),
                                             (FORM_FILES[1], "health-screening-b", "건강검진 문진표(순서 B)")])
def test_operating_forms_extract_clean_and_validate(name, qsid, title):
    res = extract_textform.run({"filename": name, "data_base64": base64.b64encode((FORMS / name).read_bytes()).decode()})
    assert res["status"] == "PARSED" and res["flags"] == []          # UNSURE도 MISSING도 없다
    b = dict(res["bank"], version=1)
    assert b["question_set_id"] == qsid and b["title"] == title and len(b["questions"]) == 31
    assert bank_validate.run({"bank": b, "version": None, "question_set_id": qsid})["errors"] == []
    pre = {q["question_id"]: (q["prerequisite"]["slot"]["question_id"] + "." + q["prerequisite"]["slot"]["slot_id"],
                              q["prerequisite"]["op"], q["prerequisite"]["value"]) for q in b["questions"] if q["prerequisite"]}
    assert pre == {"Q4_1": ("Q4.answer", "==", True), "Q5_1": ("Q5.answer", "==", True), "Q6_1": ("Q6.answer", "==", True),
                   "Q7_1": ("Q7.does_not_drink", "==", False), "Q7_2": ("Q7.does_not_drink", "==", False),
                   "Q8_2": ("Q8_1.days", "!=", 0), "Q9_2": ("Q9_1.days", "!=", 0)}
    assert {q["question_id"]: q["affected_followups"] for q in b["questions"] if q["affected_followups"]} == {
        "Q4": ["Q5"], "Q4_1": ["Q5_1"], "Q5": ["Q6"], "Q8_1": ["Q9_1"], "Q8_2": ["Q9_2"], "Q9_1": ["Q10"]}


def test_the_two_forms_differ_only_in_order_and_id():
    def qs(n):
        r = extract_textform.run({"filename": n, "data_base64": base64.b64encode((FORMS / n).read_bytes()).decode()})["bank"]
        return r, {q["question_id"]: {k: v for k, v in q.items() if k != "order"} for q in r["questions"]}
    (a, qa), (b, qb) = qs(FORM_FILES[0]), qs(FORM_FILES[1])
    assert qa == qb
    ids = lambda bank: [q["question_id"] for q in sorted(bank["questions"], key=lambda q: q["order"])]
    assert ids(b)[:2] == ["Q3", "Q4"] and ids(b)[-2:] == ["Q2.D04", "Q2.D05"] and ids(a)[0] == "Q1.D01"
