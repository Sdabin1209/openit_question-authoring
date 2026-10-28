"""사람이 읽는 미리보기. 저자는 JSON이 아니라 이것을 보고 확인한다(D31).

확신하지 못한 항목(열린 플래그)은 `[확인 필요: ...]`로 해당 위치에 표시한다. 비어 있는 값은 채우지 않고
'(비어 있음)'으로 보인다. 수치·단위·범위·위험 기준은 항상 '수치' 줄로 따로 보여 저자가 직접 읽게 한다.
"""
from __future__ import annotations

from typing import Any, Optional

from boundary_contracts import canonical
from . import classify

KIND_LABEL = {"TEXT": "문구", "CHOICE_UNIT": "선택지·단위", "SLOT": "슬롯", "BRANCH": "분기"}
FOLLOW_LABEL = {"REGENERATE_SAMPLES": "합성 샘플 재생성", "REEVALUATE_MODEL": "모델 재평가", "CLINICAL_REVIEW": "임상 검토"}
EMPTY = "(비어 있음)"


def preview_hash(draft: dict) -> str:
    """저자가 본 것과 확인하는 것이 같음을 묶는 해시: 문항 내용, 플래그 상태, 기반 버전."""
    return canonical.sha256_hash({
        "question_set_id": draft["question_set_id"],
        "base": draft.get("base"),
        "bank": draft["bank"],
        "flags": sorted([f["flag_id"], f["resolved"]] for f in draft["flags"]),
    })


def _ptr(*parts) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


class _Marks:
    def __init__(self, flags: list[dict]):
        self.flags = flags

    def at(self, path: str) -> str:
        out = []
        for f in self.flags:
            p = f["path"]
            if p == path or p.startswith(path + "/"):
                if f["resolved"]:
                    out.append(f"(저자 확인함: {f['message']})")
                else:
                    raw = f" 원문: \"{f['raw']}\"" if f.get("raw") else ""
                    out.append(f"[확인 필요: {f['message']}{raw}]")
        return (" " + " ".join(out)) if out else ""


def _num(x) -> str:
    return str(int(x)) if isinstance(x, float) and x == int(x) else str(x)


def _value(v) -> str:
    if v is None:
        return EMPTY
    if isinstance(v, str):
        return f"'{v}'"
    if isinstance(v, dict) and "amount" in v:
        return f"{_num(v['amount'])} {v.get('unit', EMPTY)}"
    if isinstance(v, dict) and "code" in v:
        return f"코드 '{v['code']}'" + (f" ({v['label']})" if v.get("label") else "")
    return str(v)


def render_text(draft: dict) -> str:
    bank = draft["bank"]
    m = _Marks(draft["flags"])
    L: list[str] = []
    qs = bank.get("questions")
    L.append(f"문항 세트 {draft['question_set_id']} 초안 {draft['draft_id']}")
    if draft.get("base"):
        L.append(f"기반 버전: {draft['base']['version']}")
    else:
        L.append("기반 버전: 없음(새 문항 세트)")
    src = draft.get("source")
    if src:
        L.append(f"출처 자료: {src['filename']} ({src['source_hash'][:19]}...) 추출기 {src.get('extractor', '없음')}")
    else:
        L.append("출처 자료: 화면 입력")
    L.append("")
    if not isinstance(qs, list) or not qs:
        L.append("문항: " + EMPTY + m.at("/questions"))
    else:
        for qi, q in enumerate(qs):
            qp = _ptr("questions", qi)
            L.append(f"문항 {q.get('question_id', EMPTY)} - {q.get('name', EMPTY)}{m.at(qp)}")
            L.append(f"  순서: {q.get('order', EMPTY)}{m.at(qp + '/order')}")
            pre = q.get("prerequisite", "__absent__")
            if pre == "__absent__":
                L.append("  선행 조건: " + EMPTY + m.at(qp + "/prerequisite"))
            elif pre is None:
                L.append("  선행 조건: 없음(항상 묻는다)" + m.at(qp + "/prerequisite"))
            else:
                s = pre.get("slot", {})
                L.append(f"  선행 조건: 문항 {s.get('question_id', EMPTY)}의 슬롯 {s.get('slot_id', EMPTY)}가 "
                         f"{_value(pre.get('value'))}일 때만 묻는다{m.at(qp + '/prerequisite')}")
            L.append(f"  질문 문장: {q.get('question_sentence', EMPTY)}{m.at(qp + '/question_sentence')}")
            L.append(f"  거절 시 설명: {q.get('refusal_explanation', EMPTY)}{m.at(qp + '/refusal_explanation')}")
            for gi, g in enumerate(q.get("guide_sentences", [])):
                L.append(f"  안내 문장 [{g.get('guide_id')}]: {g.get('sentence')}{m.at(_ptr('questions', qi, 'guide_sentences', gi))}")
            if q.get("affected_followups"):
                L.append(f"  이 답에 따라 영향받는 후속 문항: {', '.join(q['affected_followups'])}{m.at(qp + '/affected_followups')}")
            if q.get("repeat_keys"):
                L.append(f"  반복 키: {', '.join(q['repeat_keys'])}{m.at(qp + '/repeat_keys')}")
            pol = q.get("policy")
            if pol is None:
                L.append("  정책: " + EMPTY + m.at(qp + "/policy"))
            else:
                L.append(f"  수치(정책): 질문 한도 {pol.get('question_limit', EMPTY)}, 모름 {pol.get('unknown_accept_after', EMPTY)}회 후 수용, "
                         f"거절 {pol.get('refusal_accept_after', EMPTY)}회 후 수용{m.at(qp + '/policy')}")
            for si, s in enumerate(q.get("slots", [])):
                sp = _ptr("questions", qi, "slots", si)
                req = {True: "필수", False: "선택"}.get(s.get("required"), EMPTY)
                L.append(f"  슬롯 {s.get('slot_id', EMPTY)} ({req}){m.at(sp)}{m.at(sp + '/required')}")
                L.append(f"    다시 묻는 문장: {s.get('reask_sentence', EMPTY)}{m.at(sp + '/reask_sentence')}")
                for ui, u in enumerate(s.get("units", [])):
                    up = _ptr("questions", qi, "slots", si, "units", ui)
                    rng = u.get("range")
                    rtxt = f"범위 {_num(rng[0])} ~ {_num(rng[1])}" if rng else "범위 없음"
                    conv = u.get("convert_to")
                    ctxt = f", 환산: 1 {u.get('name')} = {_num(conv['factor'])} {conv['unit']}" if conv else ""
                    syn = f" (같은 말: {', '.join(u['synonyms'])})" if u.get("synonyms") else ""
                    L.append(f"    수치(단위): {u.get('name', EMPTY)}{syn}, {rtxt}{ctxt}{m.at(up)}")
                if s.get("choices"):
                    L.append(f"    선택지: {', '.join(s['choices'])}{m.at(sp + '/choices')}")
                for ci, c in enumerate(s.get("choice_catalog", [])):
                    syn = f" (같은 말: {', '.join(c['synonyms'])})" if c.get("synonyms") else ""
                    L.append(f"    선택지 [{c.get('code')}]: {c.get('label')}{syn}{m.at(_ptr('questions', qi, 'slots', si, 'choice_catalog', ci))}")
                ynm = s.get("yes_no_map", "__absent__")
                if ynm == "__absent__":
                    L.append("    예/아니오 대응: " + EMPTY + m.at(sp + "/yes_no_map"))
                elif ynm:
                    L.append(f"    예/아니오 대응: 예={ynm.get('yes')}, 아니오={ynm.get('no')}{m.at(sp + '/yes_no_map')}")
                rr = s.get("risk_rule", "__absent__")
                if rr == "__absent__":
                    L.append("    수치(위험 기준): " + EMPTY + m.at(sp + "/risk_rule"))
                elif rr is None:
                    L.append("    수치(위험 기준): 없음" + m.at(sp + "/risk_rule"))
                else:
                    when = {"IMMEDIATE": "즉시 위험 안내", "ON_RESULT": "결과 시점에 위험 안내"}.get(rr.get("response"), EMPTY)
                    L.append(f"    수치(위험 기준): 답이 {rr.get('operator')} {_value(rr.get('value'))}이면 {when}{m.at(sp + '/risk_rule')}")
    ss = bank.get("session_settings")
    L.append("")
    if ss is None:
        L.append("세션 설정: " + EMPTY + m.at("/session_settings"))
    else:
        L.append("세션 설정" + m.at("/session_settings"))
        L.append(f"  위험 문구: {', '.join(ss.get('risk_phrases', [])) or '없음'}{m.at('/session_settings/risk_phrases')}")
        L.append(f"  수치: 확인 한도 {ss.get('confirm_limit', EMPTY)}, 인식 실패 연속 한도 {ss.get('unrecognized_streak_limit', EMPTY)}, "
                 f"질문 횟수 한도 {ss.get('ask_limit', EMPTY)}{m.at('/session_settings/confirm_limit')}"
                 f"{m.at('/session_settings/unrecognized_streak_limit')}{m.at('/session_settings/ask_limit')}")
        for fi, fs in enumerate(ss.get("fixed_sentences", [])):
            L.append(f"  고정 문장 {fs.get('key')}: {fs.get('sentence')}{m.at(_ptr('session_settings', 'fixed_sentences', fi))}")
        for gi, g in enumerate(ss.get("guide_list", [])):
            L.append(f"  안내 목록 [{g.get('guide_id')}]: 기준 '{g.get('selection_criterion')}', "
                     f"{'문항별' if g.get('per_question') else '전체'}, 문장 {g.get('sentence')}, 채움값 {g.get('fill_value')}"
                     f"{m.at(_ptr('session_settings', 'guide_list', gi))}")
    # 파일 전체 수준 플래그(경로가 위 어느 줄에도 없는 것)
    orphan = [f for f in draft["flags"] if not f["resolved"] and f["path"] in ("", "/")]
    for f in orphan:
        L.append(f"[확인 필요: {f['message']}]")
    return "\n".join(L)


def change_summary(old: Optional[dict], new: dict) -> Optional[dict]:
    if old is None:
        return None
    kinds, slots, clinical = classify.diff_banks(old, new)
    follow = {f for k in kinds for f in classify.FOLLOW_UPS[k]} | ({"CLINICAL_REVIEW"} if clinical else set())
    return {
        "change_kinds": [k for k in classify.KIND_ORDER if k in kinds],
        "affected_slots": sorted(slots),
        "follow_ups": [f for f in classify.FOLLOW_UP_ORDER if f in follow],
        "text": ("변경 없음" if not kinds else
                 "변경 종류: " + ", ".join(KIND_LABEL[k] for k in classify.KIND_ORDER if k in kinds)
                 + "; 영향 슬롯: " + (", ".join(sorted(slots)) or "없음")
                 + "; 필요한 후속 작업: " + (", ".join(FOLLOW_LABEL[f] for f in classify.FOLLOW_UP_ORDER if f in follow) or "게이트 검사만")),
    }
