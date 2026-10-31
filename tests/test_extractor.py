import pytest

from question_authoring.components.extract_textform import ExtractionFailure, TextFormExtractor
from conftest import example_text

X = TextFormExtractor()


def flags(text):
    r = X.extract(text.encode(), "f.txt")
    return r, {(f.path, f.kind) for f in r.flags}


def test_example_is_clean_and_valid(validators):
    r = X.extract(example_text(), "f.txt")
    assert r.flags == []
    b = dict(r.bank, version=1)
    assert validators.errors("QuestionBank", b) == []


BASE = """세션 설정
위험 문구: 없음
확인 한도: 3
인식 실패 연속 한도: 3
질문 횟수 한도: 30
문항 Q1: 체온
순서: 1
선행 조건: 없음
질문: 체온은?
거절 설명: 필요함
정책: 질문 한도 3, 모름 2, 거절 2
슬롯 t: 필수
다시 묻기: 숫자로
"""


def test_unsure_range_not_guessed():
    r, f = flags(BASE + "단위: 도 | 범위 34-42\n예아니오: 없음\n위험 기준: 없음\n")
    assert ("/questions/0/slots/0/units/0/range", "UNSURE") in f
    assert r.bank["questions"][0]["slots"][0]["units"][0]["range"] is None   # 추정해 채우지 않는다


def test_unsure_risk_variants_not_guessed():
    for rule in ("> 40 도 즉시", ">= 40 즉시", ">= 40 도", "이상이면 위험"):
        r, f = flags(BASE + f"단위: 도\n예아니오: 없음\n위험 기준: {rule}\n")
        assert ("/questions/0/slots/0/risk_rule", "UNSURE") in f, rule
        assert "risk_rule" not in r.bank["questions"][0]["slots"][0]


def test_risk_unit_not_in_slot_units():
    r, f = flags(BASE + "단위: 도\n예아니오: 없음\n위험 기준: >= 40 도씨 즉시\n")
    assert ("/questions/0/slots/0/risk_rule/value/unit", "UNSURE") in f


def test_conversion_unsure_and_ok():
    r, f = flags(BASE + "단위: 도 | 환산: 이점이\n예아니오: 없음\n위험 기준: 없음\n")
    assert ("/questions/0/slots/0/units/0/convert_to", "UNSURE") in f
    r, f = flags(BASE + "단위: 도 | 환산: 2.2 lb\n예아니오: 없음\n위험 기준: 없음\n")
    assert r.bank["questions"][0]["slots"][0]["units"][0]["convert_to"] == {"unit": "lb", "factor": 2.2}
    assert not f


def test_missing_and_unparsed():
    r, f = flags(BASE + "단위: 도\n알 수 없는 줄입니다\n")
    p = "/questions/0/slots/0/"
    assert (p + "risk_rule", "MISSING") in f and (p + "yes_no_map", "MISSING") in f
    assert (p[:-1], "UNPARSED") in f
    assert any(x.raw == "알 수 없는 줄입니다" for x in r.flags)      # 버리지 않고 원문을 남긴다
    assert "risk_rule" not in r.bank["questions"][0]["slots"][0]


def test_missing_policy_and_session():
    r, f = flags("문항 Q1: 체온\n순서: 1\n")
    assert ("/questions/0/policy", "MISSING") in f and ("/session_settings", "MISSING") in f
    assert "policy" not in r.bank["questions"][0]


def test_failures():
    for data in (b"", b"\xff\xfe\x00bad", b"%PDF-1.7 \x00\x01", "아무 문항도 없는 글".encode()):
        with pytest.raises(ExtractionFailure):
            X.extract(data, "f.txt")


def test_registry_pluggable(store):
    """형식은 추출기 컴포넌트 하나씩 추가한다(D33). 서비스는 확장자로 고르거나 이름으로 지정하고, 맞는 것이 없으면 FALLBACK이다."""
    import types
    from question_authoring.components import extract_textform
    from question_authoring.wiring.service import AuthoringService
    from helpers import AUTHOR
    fake = types.SimpleNamespace(NAME="fake", VERSION="0", INPUT_SCHEMA="MaterialExtractRequest", OUTPUT_SCHEMA="MaterialExtraction",
                                 can_handle=lambda filename: filename.endswith(".fake"), __name__="fake",
                                 run=lambda inp: {"status": "PARSED", "bank": {"questions": []}, "flags": []})
    svc = AuthoringService(store, extractors=(extract_textform, fake), require_two_person=False)
    a = svc.ingest_material(AUTHOR, "qs-x", "a.fake", b"x")
    assert a["status"] == "PARSED" and svc.get_draft(a["draft_id"])["source"]["extractor"] == "fake"
    b = svc.ingest_material(AUTHOR, "qs-x", "a.txt", example_text())
    assert svc.get_draft(b["draft_id"])["source"]["extractor"] == "text-form"
    c = svc.ingest_material(AUTHOR, "qs-x", "a.pdf", b"%PDF")
    assert c["status"] == "FALLBACK"
    d = svc.ingest_material(AUTHOR, "qs-x", "a.txt", example_text(), extractor="nope")
    assert d["status"] == "FALLBACK"


def test_edit_paths_accept_only_json_pointer_array_positions():
    """배열 위치는 0 또는 앞에 0이 없는 10진수다(RFC 6901). int()가 받던 '-1'(뒤에서부터), ' 1', '+1', '01'로 엉뚱한 원소를 고치지 않는다."""
    from question_authoring.components.draft_edit import apply_edit
    from boundary_contracts.errors import BoundaryError
    bank = {"questions": [{"name": "A"}, {"name": "B"}]}
    for bad in ("-1", " 1", "+1", "01", "１", "a"):
        for op in ("set", "remove"):
            with pytest.raises(BoundaryError) as e:
                apply_edit(bank, {"op": op, "path": f"/questions/{bad}", "value": {"name": "X"}})
            assert e.value.code == "INVALID_RECORD"
    assert bank == {"questions": [{"name": "A"}, {"name": "B"}]}      # 거부된 편집은 아무것도 고치지 않았다
    apply_edit(bank, {"op": "set", "path": "/questions/1/name", "value": "C"})
    assert bank["questions"][1]["name"] == "C"
