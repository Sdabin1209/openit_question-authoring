"""접근 정책: 행위자의 역할이 이 작업에 허용되는지, 초안의 주인이 맞는지(4-eyes) 판단한다. 입력 AccessCheckInput -> 출력 AccessCheckResult."""
from __future__ import annotations

from boundary_contracts.errors import BoundaryError

INPUT_SCHEMA = "AccessCheckInput"
OUTPUT_SCHEMA = "AccessCheckResult"


def run(inp: dict) -> dict:
    actor = inp["actor"]
    ok_roles = set(inp["allowed_roles"])
    # 왜 reviewer_ok와 require_two_person을 함께 보나: 검수 역할이 읽기·검수를 하는 것은 4-eyes 모드에서만이다(D88 U9)
    if inp["reviewer_ok"] and inp["require_two_person"]:
        ok_roles.add(inp["reviewer_role"])
    if actor is None or actor["role"] not in ok_roles:
        raise BoundaryError("PERMISSION_DENIED", "저작 권한이 없다")
    # D88 U9: 다른 주체의 초안은 고치거나 확정하거나 버리지 못한다(4-eyes 모드)
    owner = inp["owner_id"]
    if owner is not None and inp["require_two_person"] and owner != actor["id"]:
        raise BoundaryError("PERMISSION_DENIED", "다른 저자의 초안이다")
    return {"actor": actor}
