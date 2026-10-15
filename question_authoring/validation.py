"""스키마 검증. 레코드는 쓰기 전에 boundary-contracts의 스키마로, 문항 세트는 문진 스키마 QuestionBank로 검증한다.

스키마 디렉터리는 인자로 받는다(환경 변수는 서비스 진입점만 읽는다).
"""
from __future__ import annotations

import copy
import json
from typing import Any

import jsonschema

import boundary_contracts
import survey_schema

from boundary_contracts.errors import BoundaryError

SUPPORTED_RUNTIME_CONTRACT_VERSION = 1
PIPELINE_SCHEMA_VERSION = boundary_contracts.PIPELINE_SCHEMA_VERSION


class Validators:
    """경계 계약 스키마는 boundary_contracts.validator, QuestionBank는 openit-survey-schema 패키지에서 읽는다."""

    def __init__(self, survey_version: int = SUPPORTED_RUNTIME_CONTRACT_VERSION):
        if survey_version != SUPPORTED_RUNTIME_CONTRACT_VERSION:
            raise BoundaryError("UNSUPPORTED_VERSION", f"문진 스키마 버전 {survey_version}은 지원하지 않는다"
                                f"(지원: {SUPPORTED_RUNTIME_CONTRACT_VERSION})")
        try:
            self._qb = survey_schema.load_schema("QuestionBank", survey_version)
        except KeyError as e:
            raise BoundaryError("UNSUPPORTED_VERSION", f"문진 스키마 정수 버전 {survey_version}이 설치되어 있지 않다") from e
        self.runtime_contract_version = survey_version
        self._cache: dict = {}

    def _v(self, name: str):
        if name not in self._cache:
            if name == "QuestionBank":
                self._cache[name] = jsonschema.Draft202012Validator(self._qb, format_checker=jsonschema.FormatChecker())
            else:
                try:
                    self._cache[name] = boundary_contracts.validator(name)
                except KeyError:
                    raise BoundaryError("UNSUPPORTED_VERSION", f"알 수 없는 스키마: {name}")
        return self._cache[name]

    def errors(self, name: str, record: Any) -> list[str]:
        out = []
        for e in sorted(self._v(name).iter_errors(record), key=lambda e: list(map(str, e.absolute_path))):
            loc = "/" + "/".join(map(str, e.absolute_path))
            out.append(f"{loc}: {e.message}")
        return out

    def check(self, name: str, record: Any) -> None:
        errs = self.errors(name, record)
        if errs:
            raise BoundaryError("INVALID_RECORD", f"{name} 스키마 위반: {errs[0]}", {"errors": errs[:20]})

    def question_bank_schema(self) -> dict:
        return copy.deepcopy(self._qb)


def semantic_errors(bank: dict) -> list[str]:
    """스키마가 못 잡는 구조 일관성(참조 무결성). 임상 내용은 검사하지 않는다."""
    errs: list[str] = []
    qs = bank.get("questions")
    if not isinstance(qs, list):
        return errs
    ids = [q.get("question_id") for q in qs if isinstance(q, dict)]
    for qid in sorted({i for i in ids if ids.count(i) > 1}, key=str):
        errs.append(f"문항 ID가 중복된다: {qid}")
    slots = {q.get("question_id"): [s.get("slot_id") for s in q.get("slots", []) if isinstance(s, dict)]
             for q in qs if isinstance(q, dict)}
    for qi, q in enumerate(qs):
        if not isinstance(q, dict):
            continue
        qid = q.get("question_id")
        sids = slots.get(qid, [])
        for sid in sorted({i for i in sids if sids.count(i) > 1}, key=str):
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
            cat = [c.get("code") for c in s.get("choice_catalog", []) or [] if isinstance(c, dict)]
            for c in sorted({i for i in cat if cat.count(i) > 1}, key=str):
                errs.append(f"/questions/{qi}/slots/{si}: 선택지 코드가 중복된다: {c}")
    return errs
