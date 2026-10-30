"""미리보기 해시: 저자가 본 것과 확인하는 것이 같음을 묶는다(문항 내용, 플래그 상태, 기반 버전). 입력 PreviewHashInput -> 출력 PreviewHashResult."""
from __future__ import annotations

from boundary_contracts import canonical

INPUT_SCHEMA = "PreviewHashInput"
OUTPUT_SCHEMA = "PreviewHashResult"


def run(inp: dict) -> dict:
    return {"preview_hash": canonical.sha256_hash({
        "question_set_id": inp["question_set_id"],
        "base": inp["base"],
        "bank": inp["bank"],
        "flags": sorted([f["flag_id"], f["resolved"]] for f in inp["flags"]),
    })}
