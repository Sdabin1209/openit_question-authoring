"""스키마 레지스트리와 컴포넌트 껍데기.

왜 여기서 검증하나: 컴포넌트는 자기 입출력 스키마 이름만 안다(INPUT_SCHEMA, OUTPUT_SCHEMA). 입력 검사는 스키마 검증 하나이고(DESIGN 5절),
그 검증을 부르는 일은 컴포넌트를 잇는 wiring의 몫이다(규칙 5). 내는 쪽이 내기 전에 검증한다(R7): 출력 위반은 컴포넌트 결함이다.
영역 내부 스키마는 리포 루트 `schemas/<이름>.json`, 계약 스키마는 boundary_contracts 패키지에서 읽는다(복사본 없음).
"""
from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Any, Callable

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

import boundary_contracts
from boundary_contracts.errors import BoundaryError

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"
_BASE = "https://openit.local/schemas/"


class ComponentOutputError(Exception):
    """컴포넌트가 자기 출력 스키마를 어겼다(서버 결함). 호출자에게는 UNAVAILABLE로만 나간다."""


def schema_dir() -> Path:
    # 설치된 패키지에는 schemas/가 없을 수 있어(리포 루트 기준) 환경 변수로 바꿀 수 있게 한다(이미지는 /app/schemas에 둔다)
    import os
    p = os.environ.get("QA_SCHEMA_DIR")
    return Path(p) if p else SCHEMA_DIR


def _build() -> tuple[Registry, dict]:
    reg = Registry()
    own: dict[str, dict] = {}
    cdir = boundary_contracts.schema_dir()
    for p in sorted(cdir.glob("*.schema.json")):
        reg = reg.with_resource(_BASE + p.name, Resource.from_contents(json.loads(p.read_text(encoding="utf-8")), DRAFT202012))
    for p in sorted(schema_dir().glob("*.json")):
        doc = json.loads(p.read_text(encoding="utf-8"))
        own[p.stem] = doc
        reg = reg.with_resource(_BASE + p.name, Resource.from_contents(doc, DRAFT202012))
    return reg, own


_REG: tuple[Registry, dict] | None = None
_VAL: dict[str, Any] = {}


def registry() -> tuple[Registry, dict]:
    global _REG
    if _REG is None:
        _REG = _build()
    return _REG


def _validator(name: str):
    """name: 영역 스키마 이름(`DraftEditInput`) 또는 계약 스키마 이름(`ReviewRecord`, 'AuthoringRequests#Edit' 같은 $defs 포함)."""
    if name not in _VAL:
        reg, own = registry()
        if name in own:
            uri = _BASE + name + ".json"
        else:
            base, _, frag = name.partition("#")
            uri = _BASE + base + ".schema.json" + (("#/$defs/" + frag) if frag else "")
        _VAL[name] = jsonschema.Draft202012Validator({"$ref": uri}, registry=reg, format_checker=jsonschema.FormatChecker())
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
