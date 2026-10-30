"""변경 비교: 이전 버전과 새 버전의 QuestionBank를 비교해 변경 종류 4종과 영향 슬롯, 후속 작업을 낸다. 입력 ChangeDiffInput -> 출력 ChangeDiff.

분류 규칙은 기계적이다(필드 이름 표). 임상적으로 무엇이 옳은지는 판단하지 않는다. 표에 없는 필드가 바뀌면
가장 넓게(SLOT, BRANCH, CLINICAL_REVIEW) 분류한다. 표가 스키마의 모든 필드를 덮는지는 시험이 확인한다.
"""
from __future__ import annotations

from typing import Any


TEXT, CHOICE_UNIT, SLOT, BRANCH = "TEXT", "CHOICE_UNIT", "SLOT", "BRANCH"
KIND_ORDER = [TEXT, CHOICE_UNIT, SLOT, BRANCH]

# 문항 수준 필드 -> (종류, 임상 검토 필요 여부)
QUESTION_FIELDS: dict[str, tuple[str, bool]] = {
    "name": (TEXT, False),
    "question_sentence": (TEXT, False),
    "refusal_explanation": (TEXT, False),
    "guide_sentences": (TEXT, False),
    "order": (BRANCH, False),
    "prerequisite": (BRANCH, False),
    "affected_followups": (BRANCH, False),
    "repeat_keys": (BRANCH, False),
    "policy": (BRANCH, False),
}
# 슬롯 수준 필드
SLOT_FIELDS: dict[str, tuple[str, bool]] = {
    "reask_sentence": (TEXT, False),
    "units": (CHOICE_UNIT, True),       # 단위의 범위와 환산을 담으므로 임상 검토 대상으로 표시한다
    "choices": (CHOICE_UNIT, False),
    "choice_catalog": (CHOICE_UNIT, False),
    "yes_no_map": (CHOICE_UNIT, False),
    "required": (SLOT, False),
    "risk_rule": (SLOT, True),
    # 칸 규격(survey-schema 1.2.0): 값 범위·단위를 정하므로 단위와 같게 선택지·단위, 임상 검토 대상이다(ADR 0018)
    "data_type": (CHOICE_UNIT, True),
    "unit": (CHOICE_UNIT, True),
    "minimum": (CHOICE_UNIT, True),
    "maximum": (CHOICE_UNIT, True),
    "exclusive_minimum": (CHOICE_UNIT, True),
}
SESSION_FIELDS: dict[str, tuple[str, bool]] = {
    "risk_phrases": (TEXT, True),
    "fixed_sentences": (TEXT, False),
    "guide_list": (BRANCH, False),      # 문장 변경만이면 아래에서 TEXT로 낮춘다
    "confirm_limit": (BRANCH, False),
    "unrecognized_streak_limit": (BRANCH, False),
    "ask_limit": (BRANCH, False),
}
# 'slot_id'와 'question_id'는 식별자라서 표에 없다(추가·삭제로 처리).
FOLLOW_UPS = {
    TEXT: ["REGENERATE_SAMPLES"],
    CHOICE_UNIT: ["REGENERATE_SAMPLES", "REEVALUATE_MODEL"],
    SLOT: ["REGENERATE_SAMPLES", "REEVALUATE_MODEL"],
    BRANCH: ["REGENERATE_SAMPLES", "REEVALUATE_MODEL"],
}
FOLLOW_UP_ORDER = ["REGENERATE_SAMPLES", "REEVALUATE_MODEL", "CLINICAL_REVIEW"]


def slot_ref(question_id: str, slot_id: str) -> str:
    """SlotRef 문자열 직렬화: '<question_id>.<slot_id>'. 정의(QuestionBank)에는 repeat_key가 없다(CR-0003)."""
    return f"{question_id}.{slot_id}"


def _slots(q: dict) -> dict[str, dict]:
    return {s["slot_id"]: s for s in q.get("slots", [])}


def _guide_list_kind(old: Any, new: Any) -> str:
    """안내 목록에서 문장만 달라졌으면 TEXT, 선택 기준·항목 수 등이 달라졌으면 BRANCH."""
    if not isinstance(old, list) or not isinstance(new, list) or len(old) != len(new):
        return BRANCH
    for a, b in zip(old, new):
        if a.get("guide_id") != b.get("guide_id"):
            return BRANCH
        for k in set(a) | set(b):
            if k != "sentence" and a.get(k) != b.get(k):
                return BRANCH
    return TEXT


def diff_banks(old: dict, new: dict) -> tuple[set[str], set[str], bool]:
    """(변경 종류, 영향 슬롯, 임상 검토 필요)."""
    kinds: set[str] = set()
    slots: set[str] = set()
    clinical = False

    def mark(table_entry: tuple[str, bool], affected: set[str]):
        nonlocal clinical
        kinds.add(table_entry[0])
        clinical = clinical or table_entry[1]
        slots.update(affected)

    oq = {q["question_id"]: q for q in old.get("questions", [])}
    nq = {q["question_id"]: q for q in new.get("questions", [])}
    if [q["question_id"] for q in old.get("questions", [])] != [q["question_id"] for q in new.get("questions", [])] \
            and set(oq) == set(nq):
        kinds.add(BRANCH)  # 목록 안의 순서 변경
    for qid in sorted(set(oq) | set(nq)):
        o, n = oq.get(qid), nq.get(qid)
        if o is None or n is None:
            q = n or o
            kinds.update([SLOT, BRANCH])
            slots.update(slot_ref(qid, sid) for sid in _slots(q))
            continue
        all_slots = {slot_ref(qid, sid) for sid in set(_slots(o)) | set(_slots(n))}
        for f in sorted((set(o) | set(n)) - {"question_id", "slots"}):
            if o.get(f) != n.get(f):
                if f in QUESTION_FIELDS:
                    mark(QUESTION_FIELDS[f], all_slots)
                else:  # 표에 없는 필드: 가장 넓게
                    mark((SLOT, True), all_slots)
                    kinds.add(BRANCH)
        os_, ns_ = _slots(o), _slots(n)
        for sid in sorted(set(os_) | set(ns_)):
            a, b = os_.get(sid), ns_.get(sid)
            ref = {slot_ref(qid, sid)}
            if a is None or b is None:
                kinds.add(SLOT)
                slots.update(ref)
                continue
            for f in sorted((set(a) | set(b)) - {"slot_id"}):
                if a.get(f) != b.get(f):
                    if f in SLOT_FIELDS:
                        mark(SLOT_FIELDS[f], ref)
                    else:
                        kinds.update([SLOT, BRANCH]); clinical = True; slots.update(ref)
    os_s, ns_s = old.get("session_settings", {}), new.get("session_settings", {})
    for f in sorted(set(os_s) | set(ns_s)):
        if os_s.get(f) != ns_s.get(f):
            if f == "guide_list":
                mark((_guide_list_kind(os_s.get(f), ns_s.get(f)), False), set())
            elif f in SESSION_FIELDS:
                mark(SESSION_FIELDS[f], set())
            else:
                kinds.update([SLOT, BRANCH]); clinical = True
    # 문항 순서·구조 외 최상위 필드(version 제외)
    for f in sorted((set(old) | set(new)) - {"version", "questions", "session_settings"}):
        if old.get(f) != new.get(f):
            kinds.update([SLOT, BRANCH]); clinical = True
    return kinds, slots, clinical


KIND_LABEL = {"TEXT": "문구", "CHOICE_UNIT": "선택지·단위", "SLOT": "슬롯", "BRANCH": "분기"}
FOLLOW_LABEL = {"REGENERATE_SAMPLES": "합성 샘플 재생성", "REEVALUATE_MODEL": "모델 재평가", "CLINICAL_REVIEW": "임상 검토"}
INPUT_SCHEMA = "ChangeDiffInput"
OUTPUT_SCHEMA = "ChangeDiff"


def run(inp: dict) -> dict:
    kinds, slots, clinical = diff_banks(inp["old"], inp["new"])
    follow = {f for k in kinds for f in FOLLOW_UPS[k]} | ({"CLINICAL_REVIEW"} if clinical else set())
    return {
        "change_kinds": [k for k in KIND_ORDER if k in kinds],
        "affected_slots": sorted(slots),
        "follow_ups": [f for f in FOLLOW_UP_ORDER if f in follow],
        "text": ("변경 없음" if not kinds else
                 "변경 종류: " + ", ".join(KIND_LABEL[k] for k in KIND_ORDER if k in kinds)
                 + "; 영향 슬롯: " + (", ".join(sorted(slots)) or "없음")
                 + "; 필요한 후속 작업: " + (", ".join(FOLLOW_LABEL[f] for f in FOLLOW_UP_ORDER if f in follow) or "게이트 검사만")),
    }
