"""초안 편집: 화면 입력(템플릿 양식)의 편집 항목을 문항 세트에 적용한다. 고친 경로 아래의 열린 플래그는 저자가 직접 값을 정한 것으로 닫힌다.
입력 DraftEditInput -> 출력은 계약 AuthoringResponses.Draft."""
from __future__ import annotations

import copy
import re
from typing import Any

from boundary_contracts.errors import BoundaryError

INPUT_SCHEMA = "DraftEditInput"
OUTPUT_SCHEMA = "AuthoringResponses#Draft"


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


_INDEX = re.compile(r"^(0|[1-9][0-9]*)$")   # JSON 포인터(RFC 6901 4절)의 배열 위치: 0 또는 앞에 0이 없는 10진수. int()는 '-1'(뒤에서부터), ' 1', '+1', 전각 숫자도 받아 엉뚱한 원소를 고친다


def _index(t: str) -> int:
    if not _INDEX.match(t):
        raise BoundaryError("INVALID_RECORD", f"배열 위치가 아니다: {t}")
    return int(t)


def _unescape(p: str) -> str:
    return p.replace("~1", "/").replace("~0", "~")


def _walk(root: Any, tokens: list[str], create: bool):
    cur = root
    for t in tokens:
        if isinstance(cur, list):
            try:
                cur = cur[_index(t)]
            except IndexError:
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
                parent[_index(last)] = copy.deepcopy(edit.get("value"))
            except IndexError:
                raise BoundaryError("INVALID_RECORD", f"경로를 찾을 수 없다: {path}")
        elif isinstance(parent, dict):
            parent[last] = copy.deepcopy(edit.get("value"))
        else:
            raise BoundaryError("INVALID_RECORD", f"경로를 찾을 수 없다: {path}")
    else:
        try:
            if isinstance(parent, list):
                del parent[_index(last)]
            else:
                del parent[last]
        except (KeyError, IndexError, TypeError):
            raise BoundaryError("INVALID_RECORD", f"경로를 찾을 수 없다: {path}")


def run(inp: dict) -> dict:
    d = copy.deepcopy(inp["draft"])
    if d["status"] != "OPEN":
        raise BoundaryError("FAILED_PRECONDITION", f"이미 {d['status']} 상태의 초안이다")
    new_bank = copy.deepcopy(d["bank"])
    for e in inp["edits"]:
        apply_edit(new_bank, e)
    why = structure_lock_problem(inp["base_bank"], new_bank)
    if why:
        raise BoundaryError("FAILED_PRECONDITION", why)
    for e in inp["edits"]:
        for f in d["flags"]:
            if not f["resolved"] and (f["path"] == e["path"] or f["path"].startswith(e["path"] + "/")):
                f["resolved"], f["resolved_by"], f["resolution"] = True, inp["actor_id"], "EDITED"
    d["bank"] = new_bank
    d["updated_at"] = inp["now"]
    return d
