"""초안 열기: 기반 버전의 문항 세트(없으면 빈 세트)에서 가변 초안을 만든다. 입력 DraftOpenInput -> 출력은 계약 AuthoringResponses.Draft."""
from __future__ import annotations

import copy

INPUT_SCHEMA = "DraftOpenInput"
OUTPUT_SCHEMA = "AuthoringResponses#Draft"


def run(inp: dict) -> dict:
    bank = copy.deepcopy(inp["base_bank"]) if inp["base_bank"] else {"questions": []}
    bank.pop("version", None)   # 번호는 확인 때 정해진다
    return {
        "draft_id": inp["draft_id"], "question_set_id": inp["question_set_id"], "status": "OPEN",
        "base": inp["base"], "bank": bank, "flags": [], "source": None, "fallback": None, "warnings": [],
        "created_by": inp["actor_id"], "created_at": inp["now"], "updated_at": inp["now"],
    }
