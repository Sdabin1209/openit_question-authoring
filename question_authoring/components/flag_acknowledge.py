"""플래그 확인: 추출된 값이 맞다고 저자가 직접 확인한다. 입력 FlagAcknowledgeInput -> 출력은 계약 AuthoringResponses.Draft."""
from __future__ import annotations

import copy

from boundary_contracts.errors import BoundaryError

INPUT_SCHEMA = "FlagAcknowledgeInput"
OUTPUT_SCHEMA = "AuthoringResponses#Draft"


def run(inp: dict) -> dict:
    d = copy.deepcopy(inp["draft"])
    if d["status"] != "OPEN":
        raise BoundaryError("FAILED_PRECONDITION", f"이미 {d['status']} 상태의 초안이다")
    by_id = {f["flag_id"]: f for f in d["flags"]}
    for fid in inp["flag_ids"]:
        if fid not in by_id:
            raise BoundaryError("NOT_FOUND", f"플래그가 없다: {fid}")
    for fid in inp["flag_ids"]:
        f = by_id[fid]
        if not f["resolved"]:
            f["resolved"], f["resolved_by"], f["resolution"] = True, inp["actor_id"], "ACKNOWLEDGED"
    d["updated_at"] = inp["now"]
    return d
