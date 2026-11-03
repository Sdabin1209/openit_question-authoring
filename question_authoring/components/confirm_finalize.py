"""확인 마무리: 새 버전 후보의 검증 결과와 변경 비교를 보고 저자 확인(ReviewRecord)을 만든다. 거절하는 경우: 문항 세트 검증 실패, 이전 버전과 내용이 같음.
입력 ConfirmFinalizeInput -> 출력 ConfirmFinalizeResult."""
from __future__ import annotations

from boundary_contracts.errors import BoundaryError

INPUT_SCHEMA = "ConfirmFinalizeInput"
OUTPUT_SCHEMA = "ConfirmFinalizeResult"
EXT_KEY = "question-authoring"

# 구조 잠금(U81): 둘째 버전부터 문항 ID와 칸 ID 구조는 기반 버전과 같아야 한다(문장·수치는 바꿀 수 있다). 모델이 이 구조로 학습되어 구조가 바뀌면 재학습이 필요하다.
# 왜 컴포넌트마다 같은 함수가 있나: 컴포넌트는 서로를 import하지 못한다(구조 규칙 3). 세 곳의 판정이 같은지는 tests/test_structure_lock.py가 본다.
def _structure(bank) -> set:
    out = set()
    for q in (bank or {}).get("questions", []) if isinstance(bank, dict) else []:
        if isinstance(q, dict):
            qid = q.get("question_id")
            out.add((qid, None))
            out.update((qid, s.get("slot_id")) for s in q.get("slots", []) or [] if isinstance(s, dict))
    return out


def structure_lock_problem(base_bank, bank):
    """기반이 없으면(첫 버전) None. 구조가 다르면 바뀐 ID 목록이 든 사유 문장."""
    if base_bank is None:
        return None
    old, new = _structure(base_bank), _structure(bank)
    if old == new:
        return None
    fmt = lambda xs: ", ".join(q if s is None else f"{q}.{s}" for q, s in sorted(xs, key=lambda t: (str(t[0]), str(t[1]))))[:600]
    parts = []
    if new - old:
        parts.append("추가 " + fmt(new - old))
    if old - new:
        parts.append("삭제 " + fmt(old - new))
    return "문항·칸 ID 구조는 기반 버전과 같아야 한다(" + "; ".join(parts) + "). 구조 변경은 모델 재학습이 필요해 지원하지 않는다"



def run(inp: dict) -> dict:
    why = structure_lock_problem(inp["base_bank"], inp["bank"])
    if why:
        raise BoundaryError("FAILED_PRECONDITION", why)
    errs = inp["validation_errors"]
    if errs:
        raise BoundaryError("INVALID_RECORD", f"QuestionBank 검증 실패: {errs[0]}", {"errors": errs[:20]})
    diff = inp["diff"]
    if diff is not None and not diff["change_kinds"]:
        raise BoundaryError("FAILED_PRECONDITION", "이전 버전과 내용이 같다. 새 버전을 만들지 않는다")
    d, actor = inp["draft"], inp["actor"]
    return {"review": {
        "pipeline_schema_version": inp["pipeline_schema_version"], "review_id": "rv-" + d["draft_id"],
        "question_set": inp["ref"], "reviewer_role": actor["role"], "reviewer_id": actor["id"],
        # 4-eyes(기본): 저자의 확인은 내용 확인이고 임상 근거 승인이 아니다. 임상 승인은 검수 역할이 add_review로만 한다(D88 U9)
        "decision": "APPROVED", "clinical_basis_approved": not inp["require_two_person"], "reviewed_at": inp["at"],
        "ext": {EXT_KEY: {
            "kind": "AUTHOR_CONFIRMATION", "draft_id": d["draft_id"], "preview_hash": inp["preview_hash"],
            "source": d["source"] or {"kind": "screen"},
            "acknowledged_flags": [f["flag_id"] for f in d["flags"] if f.get("resolution") == "ACKNOWLEDGED"],
            "edited_flags": [f["flag_id"] for f in d["flags"] if f.get("resolution") == "EDITED"],
        }}}}
