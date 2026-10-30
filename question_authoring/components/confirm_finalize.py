"""확인 마무리: 새 버전 후보의 검증 결과와 변경 비교를 보고 저자 확인(ReviewRecord)을 만든다. 거절하는 경우: 문항 세트 검증 실패, 이전 버전과 내용이 같음.
입력 ConfirmFinalizeInput -> 출력 ConfirmFinalizeResult."""
from __future__ import annotations

from boundary_contracts.errors import BoundaryError

INPUT_SCHEMA = "ConfirmFinalizeInput"
OUTPUT_SCHEMA = "ConfirmFinalizeResult"
EXT_KEY = "question-authoring"


def run(inp: dict) -> dict:
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
