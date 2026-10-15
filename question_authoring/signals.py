"""개선 신호 수신함(읽기 전용). ops-feedback의 실패 사례(I7)를 읽어 문항별 개선 신호를 만든다.

- 문항 세트 수준: FailureCase.question_set.id로 센다(trace가 없어도 된다).
- 문항 수준: 비식별 trace의 planner_output.directive(와 awaiting)의 targets에 있는 question_id를 쓴다.
  없으면 건너뛰고 사유를 센다. 추정하지 않는다.
- 담는 것은 건수, 유형 분포, ID뿐이다. 발화 문장과 trace 본문은 담지 않는다(O19 미정).
- 이 모듈은 아무것도 고치지 않는다. 신호를 보고 문항을 고치는 것은 저자의 판단이다.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

from boundary_contracts.errors import BoundaryError

DEFAULT_MAX_CASES = 2000
CONFUSION_MIN = 2
SKIP_NO_SET = "NO_QUESTION_SET"          # 사례에 question_set이 없다(문항 세트 수준에서도 제외)
SKIP_NO_QUESTION = "NO_QUESTION_ID"      # trace에 문항 ID가 없다(문항 수준에서만 제외)
SKIP_TRACE = "TRACE_UNREADABLE"          # trace를 읽지 못했다(문항 수준에서만 제외)


class FailureSource(Protocol):
    def list_cases(self, *, review_status=None, latest_only: bool = True, cursor=None, limit: int = 100): ...
    def get_trace(self, deidentified_trace_ref: str) -> dict: ...


def question_ids_from_trace(trace: Any) -> set[str]:
    """비식별 trace에서 이 턴이 가리킨 문항 ID. 구조(열거값, 식별자)만 읽는다."""
    out: set[str] = set()
    if not isinstance(trace, dict):
        return out
    po = trace.get("planner_output")
    d = po.get("directive") if isinstance(po, dict) else None
    if not isinstance(d, dict):
        return out
    groups = [d.get("targets")]
    if isinstance(d.get("awaiting"), dict):
        groups.append(d["awaiting"].get("targets"))
    for g in groups:
        for t in g if isinstance(g, list) else []:
            if isinstance(t, dict) and isinstance(t.get("question_id"), str) and t["question_id"]:
                out.add(t["question_id"])
    return out


def _dist(c: Counter) -> dict:
    return dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))


def build_signals(source: FailureSource, *, question_set_id: Optional[str] = None,
                  review_status: Optional[str] = None, max_cases: int = DEFAULT_MAX_CASES,
                  confusion_min: int = CONFUSION_MIN, now: Optional[datetime] = None) -> dict:
    cases, cur, truncated = [], None, False
    while True:
        items, cur = source.list_cases(review_status=review_status, latest_only=True, cursor=cur, limit=min(500, max_cases))
        cases += items
        if len(cases) >= max_cases:
            truncated = cur is not None or len(cases) > max_cases
            cases = cases[:max_cases]
            break
        if cur is None:
            break
    skipped: Counter = Counter()
    sets: dict[str, dict] = {}
    trace_cache: dict[str, set[str]] = {}
    for c in cases:
        ref = c.get("question_set")
        if not isinstance(ref, dict) or "id" not in ref:
            skipped[SKIP_NO_SET] += 1
            continue
        qs = ref["id"]
        if question_set_id is not None and qs != question_set_id:
            continue
        s = sets.setdefault(qs, {"versions": Counter(), "types": Counter(), "cases": 0, "confirmed": 0,
                                 "questions": defaultdict(lambda: {"types": Counter(), "cases": 0, "confirmed": 0})})
        confirmed = c.get("review_status") == "CONFIRMED"
        ftype = c.get("failure_type", "UNKNOWN")
        s["cases"] += 1; s["confirmed"] += confirmed
        s["types"][ftype] += 1
        s["versions"][str(ref.get("version"))] += 1
        tref = c.get("deidentified_trace_ref")
        if tref not in trace_cache:
            try:
                trace_cache[tref] = question_ids_from_trace(source.get_trace(tref))
            except BoundaryError:
                trace_cache[tref] = None            # type: ignore[assignment]
        qids = trace_cache[tref]
        if qids is None:
            skipped[SKIP_TRACE] += 1
        elif not qids:
            skipped[SKIP_NO_QUESTION] += 1
        else:
            for q in qids:
                e = s["questions"][q]
                e["cases"] += 1; e["confirmed"] += confirmed; e["types"][ftype] += 1
    out_sets = []
    for qs in sorted(sets):
        s = sets[qs]
        qlist = []
        for q, e in s["questions"].items():
            qlist.append({
                "question_id": q, "case_count": e["cases"], "confirmed_count": e["confirmed"],
                "failure_types": _dist(e["types"]),
                "confusions": [{"failure_type": t, "count": n} for t, n in _dist(e["types"]).items() if n >= confusion_min],
            })
        qlist.sort(key=lambda x: (-x["case_count"], x["question_id"]))
        out_sets.append({"question_set_id": qs, "case_count": s["cases"], "confirmed_count": s["confirmed"],
                         "failure_types": _dist(s["types"]), "versions": dict(sorted(s["versions"].items())),
                         "questions": qlist})
    return {
        "status": "OK",
        "generated_at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cases_read": len(cases), "truncated": truncated, "confusion_min": confusion_min,
        "question_sets": out_sets, "skipped": dict(sorted(skipped.items())),
        "note": "읽기 전용 신호다. 문항을 고칠지는 저자가 정한다. 발화 문장은 담지 않는다.",
    }
