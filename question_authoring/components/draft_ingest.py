"""자료 등록 결과 반영: 추출 결과(또는 읽지 못한 사유)를 열린 초안에 싣는다. 입력 DraftIngestInput -> 출력 DraftIngestResult."""
from __future__ import annotations

import copy

from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError

INPUT_SCHEMA = "DraftIngestInput"
OUTPUT_SCHEMA = "DraftIngestResult"
FALLBACK_GUIDANCE = "자료를 읽지 못했다. 화면 입력으로 이어서 작성할 수 있다. 원본 해시는 출처로 남는다."

# 구조 잠금(U81): 둘째 버전부터 문항 ID와 칸 ID 구조는 기반 버전과 같아야 한다(문장·수치는 바꿀 수 있다). 모델이 이 구조로 학습되어 구조가 바뀌면 재학습이 필요하다.
# 왜 컴포넌트마다 같은 함수가 있나: 컴포넌트는 서로를 import하지 못한다(구조 규칙 3). 세 곳의 판정이 같은지는 tests/test_structure_lock.py가 본다.
def _structure(bank) -> set:
    out = set()
    for q in (bank or {}).get("questions", []) if isinstance(bank, dict) else []:
        if isinstance(q, dict):
            qid = q.get("question_id")
            out.add((qid, None))
            out.update((qid, s.get("slot_id")) for s in q.get("slots", []) or [] if isinstance(s, dict))
    return out


def structure_lock_problem(base_bank, bank):
    """기반이 없으면(첫 버전) None. 구조가 다르면 바뀐 ID 목록이 든 사유 문장."""
    if base_bank is None:
        return None
    old, new = _structure(base_bank), _structure(bank)
    if old == new:
        return None
    fmt = lambda xs: ", ".join(q if s is None else f"{q}.{s}" for q, s in sorted(xs, key=lambda t: (str(t[0]), str(t[1]))))[:600]
    parts = []
    if new - old:
        parts.append("추가 " + fmt(new - old))
    if old - new:
        parts.append("삭제 " + fmt(old - new))
    return "문항·칸 ID 구조는 기반 버전과 같아야 한다(" + "; ".join(parts) + "). 구조 변경은 모델 재학습이 필요해 지원하지 않는다"



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
    bid = ex["bank"].get("question_set_id")
    if bid is not None and bid != d["question_set_id"]:
        # 양식의 문진표 ID와 올린 문항 세트 ID(I1의 {id})가 다르면 거절한다: 다른 문진표 자료가 이 세트의 새 버전이 되는 사고를 막는다(ADR 0027)
        raise BoundaryError("FAILED_PRECONDITION", f"양식의 문진표 ID '{bid}'가 올린 문항 세트 ID '{d['question_set_id']}'와 다르다")
    why = structure_lock_problem(inp["base_bank"], ex["bank"])
    if why:
        raise BoundaryError("FAILED_PRECONDITION", why)
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
