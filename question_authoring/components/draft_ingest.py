"""자료 등록 결과 반영: 추출 결과(또는 읽지 못한 사유)를 열린 초안에 싣는다. 입력 DraftIngestInput -> 출력 DraftIngestResult."""
from __future__ import annotations

import copy

from boundary_contracts import canonical

INPUT_SCHEMA = "DraftIngestInput"
OUTPUT_SCHEMA = "DraftIngestResult"
FALLBACK_GUIDANCE = "자료를 읽지 못했다. 화면 입력으로 이어서 작성할 수 있다. 원본 해시는 출처로 남는다."


def run(inp: dict) -> dict:
    d = copy.deepcopy(inp["draft"])
    ex = inp["extraction"]
    d["warnings"] = inp["warnings"]
    d["source"] = {"kind": "material", **inp["source"]}
    d["updated_at"] = inp["now"]
    if inp["extractor"] is not None:
        d["source"].update(extractor=inp["extractor"]["name"], extractor_version=inp["extractor"]["version"])
    if ex["status"] == "FAILED":
        # 왜 extractor를 넣지 않나: 추출기를 고르기 전에 실패하면 이름이 없다. 계약 Source.extractor는 문자열이라 None을 쓰면 ReviewExt 검사가 거부한다
        d["fallback"] = {"reason": ex["reason"], "guidance": inp.get("guidance") or FALLBACK_GUIDANCE}
        return {"draft": d, "status": "FALLBACK", "reason": ex["reason"], "open_flags": 0}
    d["bank"] = ex["bank"]
    flags, seen = [], {}
    for f in ex["flags"]:
        base_id = "fl-" + canonical.sha256_hash([f["path"], f["kind"], f["message"], f["raw"]])[7:19]
        n = seen.get(base_id, 0)
        seen[base_id] = n + 1
        flags.append({"flag_id": base_id if n == 0 else f"{base_id}-{n}", "path": f["path"], "kind": f["kind"],
                      "message": f["message"], "raw": f["raw"], "resolved": False})
    d["flags"] = flags
    return {"draft": d, "status": "PARSED", "open_flags": len(flags)}
