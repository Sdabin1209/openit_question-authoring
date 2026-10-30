"""초안 버리기: 확인하지 않고 버린다. 버전이 아니므로 ReviewRecord는 만들지 않는다. 입력 DraftDiscardInput -> 출력은 계약 AuthoringResponses.Draft."""
from __future__ import annotations

import copy

from boundary_contracts.errors import BoundaryError

INPUT_SCHEMA = "DraftDiscardInput"
OUTPUT_SCHEMA = "AuthoringResponses#Draft"


def run(inp: dict) -> dict:
    d = copy.deepcopy(inp["draft"])
    if d["status"] != "OPEN":
        raise BoundaryError("FAILED_PRECONDITION", f"이미 {d['status']} 상태의 초안이다")
    d["status"], d["discard_reason"], d["discarded_by"] = "DISCARDED", inp["reason"], inp["actor_id"]
    d["updated_at"] = inp["now"]
    return d
