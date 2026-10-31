"""문항 세트 검증: 문진 스키마 QuestionBank 검사와 참조 무결성. 입력 BankValidateInput -> 출력 BankValidateResult.

임상 내용은 검사하지 않는다. 문진 스키마는 정수 버전에 정확히 일치해야 한다(D14).
"""
from __future__ import annotations

import copy
from collections import Counter
from typing import Any

import jsonschema

import survey_schema
from boundary_contracts.errors import BoundaryError

INPUT_SCHEMA = "BankValidateInput"
OUTPUT_SCHEMA = "BankValidateResult"
# 문진 스키마의 정수 CONTRACT_VERSION(런타임 호환 번호). openit-survey-schema 3.0.0 패키지의 CONTRACT_VERSION 파일이 1이다(D14: 정확히 일치해야 한다). 올릴 때는 패키지와 함께 바꾼다
SUPPORTED_RUNTIME_CONTRACT_VERSION = 1
_V = None


def _validator():
    global _V
    if _V is None:
        try:
            qb = survey_schema.load_schema("QuestionBank", SUPPORTED_RUNTIME_CONTRACT_VERSION)
        except KeyError as e:
            raise BoundaryError("UNSUPPORTED_VERSION", f"문진 스키마 정수 버전 {SUPPORTED_RUNTIME_CONTRACT_VERSION}이 설치되어 있지 않다") from e
        _V = jsonschema.Draft202012Validator(qb, format_checker=jsonschema.FormatChecker())
    return _V


def question_bank_errors(bank: Any) -> list[str]:
    v = _validator()
    return [f"/{'/'.join(map(str, e.absolute_path))}: {e.message}"
            for e in sorted(v.iter_errors(bank), key=lambda e: list(map(str, e.absolute_path)))]


def run(inp: dict) -> dict:
    bank = copy.deepcopy(inp["bank"])
    if inp["version"] is not None:
        bank["version"] = inp["version"]   # 미리보기는 번호가 정해지기 전이라 0으로 구조만 본다
    return {"errors": question_bank_errors(bank) + semantic_errors(bank)}


def semantic_errors(bank: dict) -> list[str]:
    """스키마가 못 잡는 구조 일관성(참조 무결성). 임상 내용은 검사하지 않는다."""
    errs: list[str] = []
    qs = bank.get("questions")
    if not isinstance(qs, list):
        return errs
    # 중복 찾기는 Counter로 한 번 훑는다(문항 2000개까지 허용하는데 list.count는 제곱 시간)
    ids = Counter(q.get("question_id") for q in qs if isinstance(q, dict))
    for qid in sorted((i for i, n in ids.items() if n > 1), key=str):
        errs.append(f"문항 ID가 중복된다: {qid}")
    slots = {q.get("question_id"): [s.get("slot_id") for s in q.get("slots", []) if isinstance(s, dict)]
             for q in qs if isinstance(q, dict)}
    for qi, q in enumerate(qs):
        if not isinstance(q, dict):
            continue
        qid = q.get("question_id")
        sids = Counter(slots.get(qid, []))
        for sid in sorted((i for i, n in sids.items() if n > 1), key=str):
            errs.append(f"/questions/{qi}: 슬롯 ID가 중복된다: {sid}")
        pre = q.get("prerequisite")
        if isinstance(pre, dict) and isinstance(pre.get("slot"), dict):
            ps = pre["slot"]
            if ps.get("question_id") not in slots:
                errs.append(f"/questions/{qi}/prerequisite: 없는 문항을 가리킨다: {ps.get('question_id')}")
            elif ps.get("slot_id") not in slots[ps.get("question_id")]:
                errs.append(f"/questions/{qi}/prerequisite: 없는 슬롯을 가리킨다: {ps.get('question_id')}.{ps.get('slot_id')}")
        for f in q.get("affected_followups", []) or []:
            if f not in slots:
                errs.append(f"/questions/{qi}/affected_followups: 없는 문항을 가리킨다: {f}")
        for si, s in enumerate(q.get("slots", []) or []):
            if not isinstance(s, dict):
                continue
            cat = Counter(c.get("code") for c in s.get("choice_catalog", []) or [] if isinstance(c, dict))
            for c in sorted((i for i, n in cat.items() if n > 1), key=str):
                errs.append(f"/questions/{qi}/slots/{si}: 선택지 코드가 중복된다: {c}")
    return errs
