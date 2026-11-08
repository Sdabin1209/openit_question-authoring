"""스키마 레지스트리와 컴포넌트 껍데기.

왜 여기서 검증하나: 컴포넌트는 자기 입출력 스키마 이름만 안다(INPUT_SCHEMA, OUTPUT_SCHEMA). 입력 검사는 스키마 검증 하나이고(DESIGN 5절),
그 검증을 부르는 일은 컴포넌트를 잇는 wiring의 몫이다(규칙 5). 내는 쪽이 내기 전에 검증한다(R7): 출력 위반은 컴포넌트 결함이다.
영역 내부 스키마는 리포 루트 `schemas/<이름>.json`, 계약 스키마는 boundary_contracts 패키지에서 읽는다(복사본 없음).
"""
from __future__ import annotations

import json
import re
import types
from pathlib import Path
from typing import Any, Callable

import boundary_contracts
from boundary_contracts.errors import BoundaryError
from boundary_contracts.schema_sources import CONTRACT_URI

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"


class ComponentOutputError(Exception):
    """컴포넌트가 자기 출력 스키마를 어겼다(서버 결함). 호출자에게는 UNAVAILABLE로만 나간다."""


def schema_dir() -> Path:
    # 설치된 패키지에는 schemas/가 없을 수 있어(리포 루트 기준) 환경 변수로 바꿀 수 있게 한다(이미지는 /app/schemas에 둔다)
    import os
    p = os.environ.get("QA_SCHEMA_DIR")
    return Path(p) if p else SCHEMA_DIR


_OWN_REF = re.compile(r"^([A-Za-z0-9_]+)\.json$")   # 영역 스키마끼리의 참조(파일 이름만, 조각 없음)


def _own(name: str) -> dict:
    return json.loads((schema_dir() / (name + ".json")).read_text(encoding="utf-8"))


def _bundle(name: str) -> dict:
    """영역 스키마 하나를 계약 검증기가 받는 한 문서로 묶는다. 영역 스키마끼리의 참조(`Actor.json`)는 `$defs` 로 넣고,
    계약 참조(`_common.schema.json#/...`)는 그대로 둔다: 루트 `$id` 를 계약 안정 URI 와 같은 폴더에 두어 상대 참조가 계약 스키마로 풀린다.
    왜 묶나: 계약의 schema_validator(5.0.1)는 계약 registry 만 쓰므로 영역 스키마를 따로 등록할 수 없다."""
    defs: dict[str, dict] = {}

    def rewrite(node):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k in ("$id", "$schema"):
                    continue
                if k == "$ref" and isinstance(v, str) and _OWN_REF.match(v):
                    dep = _OWN_REF.match(v).group(1)
                    if dep not in defs:
                        defs[dep] = {}                    # 순환 방지: 자리를 먼저 잡는다
                        defs[dep] = rewrite(_own(dep))
                    out[k] = "#/$defs/" + dep
                else:
                    out[k] = rewrite(v)
            return out
        if isinstance(node, list):
            return [rewrite(v) for v in node]
        return node

    root = rewrite(_own(name))
    root["$id"] = CONTRACT_URI + "area-" + name + ".json"
    if defs:
        root["$defs"] = {**root.get("$defs", {}), **defs}
    return root


_VAL: dict[str, Any] = {}


def _validator(name: str):
    """name: 영역 스키마 이름(`DraftEditInput`) 또는 계약 스키마 이름(`ReviewRecord`, 'AuthoringRequests#Edit' 같은 $defs 포함)."""
    if name not in _VAL:
        if (schema_dir() / (name + ".json")).exists():
            schema = _bundle(name)
        else:
            base, _, frag = name.partition("#")
            schema = {"$ref": CONTRACT_URI + base + ".schema.json" + (("#/$defs/" + frag) if frag else "")}
        # 왜 schema_validator: 계약 registry(안정 URI)·형식 검사 강제·비유한 수 거부를 한 곳에서 받는다(계약 5.0.1, D117). 직접 만든 Draft202012Validator 는 쓰지 않는다
        _VAL[name] = boundary_contracts.schema_validator(schema)
    return _VAL[name]


def problems(name: str, value: Any) -> list[str]:
    v = _validator(name)
    return ["/" + "/".join(map(str, e.absolute_path)) + f": {e.validator} 규칙 위반"
            for e in sorted(v.iter_errors(value), key=lambda e: list(map(str, e.absolute_path)))]


def check(name: str, value: Any) -> None:
    """입력 검증. 위반은 INVALID_RECORD다. 메시지에는 위치와 규칙 이름만 쓴다(값에 자료가 섞일 수 있다)."""
    errs = problems(name, value)
    if errs:
        raise BoundaryError("INVALID_RECORD", f"{name} 위반: {errs[0]}", {"errors": errs[:20]})


def bind(module: types.ModuleType) -> Callable[..., Any]:
    """컴포넌트 모듈(INPUT_SCHEMA, OUTPUT_SCHEMA, run)을 '입력 검증 -> run -> 출력 검증' 함수로 만든다."""
    in_s, out_s = module.INPUT_SCHEMA, module.OUTPUT_SCHEMA
    name = getattr(module, "__name__", type(module).__name__)

    def call(inp: Any) -> Any:
        check(in_s, inp)
        out = module.run(inp)
        errs = problems(out_s, out)
        if errs:
            raise ComponentOutputError(f"{name} 출력이 {out_s}를 어겼다: {errs[0]}")
        return out
    call.__name__ = name.rsplit(".", 1)[-1]
    call.component = module
    return call
