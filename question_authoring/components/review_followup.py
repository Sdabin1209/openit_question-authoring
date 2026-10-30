"""이후 검수: 만들어진 버전에 검수를 덧붙인다(두 번째 검수자 또는 승인 철회). 이전 검수는 지우지 않는다. 재시도 멱등.
입력 ReviewFollowupInput -> 출력 ReviewFollowupResult. 거절은 BoundaryError(임상 승인은 검수 역할만, 자기 확인은 스스로 승인 불가, review_id 충돌)."""
from __future__ import annotations

from boundary_contracts.errors import BoundaryError

INPUT_SCHEMA = "ReviewFollowupInput"
OUTPUT_SCHEMA = "ReviewFollowupResult"
EXT_KEY = "question-authoring"
REVIEWER_ROLE = "clinical-reviewer"


def run(inp: dict) -> dict:
    actor, two = inp["actor"], inp["require_two_person"]
    approved = inp["clinical_basis_approved"]
    if two and approved:
        # 임상 근거 승인: 검수 역할이고, 이 버전을 만든(확인한) 주체와 달라야 한다
        if actor["role"] != REVIEWER_ROLE:
            raise BoundaryError("PERMISSION_DENIED", "임상 근거 승인은 검수 역할(clinical-reviewer)만 한다")
        for old in inp["existing_reviews"]:
            ext = (old.get("ext") or {}).get(EXT_KEY) or {}
            if ext.get("kind") == "AUTHOR_CONFIRMATION" and old.get("reviewer_id") == actor["id"]:
                raise BoundaryError("PERMISSION_DENIED", "자신이 확인한 버전을 스스로 임상 승인할 수 없다(저자와 검수자는 달라야 한다)")
    rec = {
        "pipeline_schema_version": inp["pipeline_schema_version"], "review_id": inp["review_id"] or inp["new_review_id"],
        "question_set": inp["ref"], "reviewer_role": actor["role"], "reviewer_id": actor["id"], "decision": inp["decision"],
        "clinical_basis_approved": approved, "reviewed_at": inp["now"],
        "ext": {EXT_KEY: {"kind": "FOLLOW_UP_REVIEW"}},
    }
    if inp.get("reason"):
        rec["reason"] = inp["reason"]
    if inp["review_id"]:  # 재시도 멱등: 같은 review_id에 같은 판단이면 기존 것을 돌려준다
        for old in inp["existing_reviews"]:
            if old["review_id"] == inp["review_id"]:
                keys = ("reviewer_role", "reviewer_id", "decision", "clinical_basis_approved", "reason")
                if all(old.get(k) == rec.get(k) for k in keys):
                    return {"action": "RETURN_EXISTING", "review": old}
                raise BoundaryError("CONFLICT", f"같은 review_id에 다른 내용: {inp['review_id']}")
    return {"action": "STORE", "review": rec}
