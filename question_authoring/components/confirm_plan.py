"""확인 계획: 저자의 확인 요청을 받아 다시 응답할지(멱등), 새 버전을 만들지, 거절할지 정한다. 새 버전이면 번호와 시각을 예약한다(재시도가 같은 레코드를 내게).
입력 ConfirmPlanInput -> 출력 ConfirmPlan. 거절은 BoundaryError다(조건이 초안 상태와 저장소 최신 버전을 함께 봐야 해서 스키마 밖)."""
from __future__ import annotations

from boundary_contracts.errors import BoundaryError

INPUT_SCHEMA = "ConfirmPlanInput"
OUTPUT_SCHEMA = "ConfirmPlan"


def run(inp: dict) -> dict:
    d, submitted = inp["draft"], inp["preview_hash"]
    if d["status"] == "CONFIRMED":
        if d["confirmed"]["preview_hash"] == submitted:
            return {"action": "REPLAY", "ref": d["confirmed"]["ref"]}      # 멱등
        raise BoundaryError("CONFLICT", "이미 다른 미리보기로 확인된 초안이다")
    if d["status"] != "OPEN":
        raise BoundaryError("FAILED_PRECONDITION", f"{d['status']} 상태의 초안은 확인할 수 없다")
    if inp["current_preview_hash"] != submitted:
        raise BoundaryError("FAILED_PRECONDITION", "미리보기 이후 초안이 바뀌었다. 미리보기를 다시 확인해야 한다")
    open_flags = [f for f in d["flags"] if not f["resolved"]]
    if open_flags:
        raise BoundaryError("FAILED_PRECONDITION", f"확인하지 않은 항목이 {len(open_flags)}건 있다",
                            {"flag_ids": [f["flag_id"] for f in open_flags]})
    latest = inp["latest"]
    reserved = d.get("confirming")
    if not reserved and latest != d["base"]:
        raise BoundaryError("FAILED_PRECONDITION", "초안을 만든 뒤 새 버전이 생겼다. 최신 버전에서 다시 시작해야 한다")
    if reserved:
        return {"action": "PROCEED", "reservation": reserved, "reserved_now": False}
    return {"action": "PROCEED", "reserved_now": True,
            "reservation": {"version": (int(latest["version"]) + 1) if latest else 1, "at": inp["now"]}}
