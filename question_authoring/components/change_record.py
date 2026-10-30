"""변경 분류 레코드: 비교 결과(ChangeDiff)와 새 버전 ref로 계약 ChangeClassification을 만든다. 입력 ChangeRecordInput -> 출력 ChangeRecord."""
from __future__ import annotations

from boundary_contracts import canonical

INPUT_SCHEMA = "ChangeRecordInput"
OUTPUT_SCHEMA = "ChangeRecord"
EXT_KEY = "question-authoring"


def run(inp: dict) -> dict:
    d = inp["diff"]
    return {
        "pipeline_schema_version": inp["pipeline_schema_version"],
        "classification_id": "cc-" + canonical.sha256_hash(inp["to"])[7:31],
        "to": inp["to"],
        "from": inp["from"],
        "change_kinds": d["change_kinds"],
        "affected_slots": d["affected_slots"],
        "follow_ups": d["follow_ups"],
        "created_at": inp["created_at"],
        "ext": {EXT_KEY: {"draft_id": inp["draft_id"]}},
    }
