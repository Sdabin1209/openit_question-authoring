"""초안 편집: 화면 입력(템플릿 양식)의 편집 항목을 문항 세트에 적용한다. 고친 경로 아래의 열린 플래그는 저자가 직접 값을 정한 것으로 닫힌다.
입력 DraftEditInput -> 출력은 계약 AuthoringResponses.Draft."""
from __future__ import annotations

import copy
from typing import Any

from boundary_contracts.errors import BoundaryError

INPUT_SCHEMA = "DraftEditInput"
OUTPUT_SCHEMA = "AuthoringResponses#Draft"


def _unescape(p: str) -> str:
    return p.replace("~1", "/").replace("~0", "~")


def _walk(root: Any, tokens: list[str], create: bool):
    cur = root
    for t in tokens:
        if isinstance(cur, list):
            try:
                cur = cur[int(t)]
            except (ValueError, IndexError):
                raise BoundaryError("INVALID_RECORD", f"경로를 찾을 수 없다: {t}")
        elif isinstance(cur, dict):
            if t not in cur:
                if create:
                    cur[t] = {}
                else:
                    raise BoundaryError("INVALID_RECORD", f"경로를 찾을 수 없다: {t}")
            cur = cur[t]
        else:
            raise BoundaryError("INVALID_RECORD", f"경로를 찾을 수 없다: {t}")
    return cur


def apply_edit(bank: dict, edit: dict) -> None:
    """edit = {op: 'set'|'remove'|'append', path: '/questions/0/...', value?}. 경로는 JSON 포인터. 경로가 문서에 있는지는 문서를 봐야 알 수 있다(스키마 밖)."""
    op, path = edit["op"], edit["path"]
    tokens = [_unescape(t) for t in path[1:].split("/")]
    parent = _walk(bank, tokens[:-1], create=(op == "set"))
    last = tokens[-1]
    if op == "append":
        target = parent.get(last) if isinstance(parent, dict) else None
        if not isinstance(target, list):
            raise BoundaryError("INVALID_RECORD", f"목록이 아니다: {path}")
        target.append(copy.deepcopy(edit.get("value")))
    elif op == "set":
        if isinstance(parent, list):
            try:
                parent[int(last)] = copy.deepcopy(edit.get("value"))
            except (ValueError, IndexError):
                raise BoundaryError("INVALID_RECORD", f"경로를 찾을 수 없다: {path}")
        elif isinstance(parent, dict):
            parent[last] = copy.deepcopy(edit.get("value"))
        else:
            raise BoundaryError("INVALID_RECORD", f"경로를 찾을 수 없다: {path}")
    else:
        try:
            if isinstance(parent, list):
                del parent[int(last)]
            else:
                del parent[last]
        except (KeyError, ValueError, IndexError, TypeError):
            raise BoundaryError("INVALID_RECORD", f"경로를 찾을 수 없다: {path}")


def run(inp: dict) -> dict:
    d = copy.deepcopy(inp["draft"])
    if d["status"] != "OPEN":
        raise BoundaryError("FAILED_PRECONDITION", f"이미 {d['status']} 상태의 초안이다")
    new_bank = copy.deepcopy(d["bank"])
    for e in inp["edits"]:
        apply_edit(new_bank, e)
    for e in inp["edits"]:
        for f in d["flags"]:
            if not f["resolved"] and (f["path"] == e["path"] or f["path"].startswith(e["path"] + "/")):
                f["resolved"], f["resolved_by"], f["resolution"] = True, inp["actor_id"], "EDITED"
    d["bank"] = new_bank
    d["updated_at"] = inp["now"]
    return d
