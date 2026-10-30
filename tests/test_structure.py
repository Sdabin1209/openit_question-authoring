"""구조 검사(DESIGN.md 6절, D117). 헤드의 head/scripts/structure-check.py와 같은 기준을 이 영역 안에서 강제한다."""
import ast
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

import pytest

from question_authoring.wiring import schemas

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / "question_authoring"
COMPONENTS = sorted(p for p in (PKG / "components").glob("*.py") if p.name != "__init__.py")
HTTP_MODULES = {"urllib.request", "http.client", "http.server", "requests", "waitress", "wsgiref", "boundary_contracts.clients", "openit_link"}
ENTRY = re.compile(r"(^|/)(__main__|main|app|server|config|settings|migrate|cli|index)\.py$|(^|/)migrations?/")


def imports(path):
    mods = []
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            mods += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            base = ("." * n.level) + (n.module or "")
            mods.append(base)
            mods += [base + "." + a.name for a in n.names]
    return mods


def test_1_standard_layout_exists():
    for d in ("schemas", "question_authoring/types", "question_authoring/components", "question_authoring/wiring"):
        assert any((ROOT / d).iterdir()), d + " 없음"


def test_2_components_do_not_import_each_other_or_wiring():
    names = {p.stem for p in COMPONENTS}
    for p in COMPONENTS:
        for m in imports(p):
            parts = set(x for x in m.split(".") if x)
            assert not (parts & (names - {p.stem})), f"{p.name}가 다른 컴포넌트를 import: {m}"
            assert "wiring" not in parts, f"{p.name}가 wiring을 import: {m}"   # 컴포넌트는 연결 방법을 모른다(규칙 3·4)


def test_3_http_only_in_link_components():
    for p in (q for q in PKG.rglob("*.py") if "types" not in q.parts):
        if p.name.startswith("link_") or ENTRY.search(str(p.relative_to(PKG))):
            continue
        for m in imports(p):
            assert not any(m == h or m.startswith(h + ".") for h in HTTP_MODULES), f"{p.relative_to(ROOT)}: {m}"


def test_4_every_schema_has_valid_and_invalid_examples_and_they_behave():
    files = sorted((ROOT / "schemas").glob("*.json"))
    assert files
    for f in files:
        name = f.stem
        valid = sorted((ROOT / "schemas" / "examples").glob(f"{name}.valid*.json"))
        invalid = sorted((ROOT / "schemas" / "examples").glob(f"{name}.invalid*.json"))
        assert valid and invalid, f"{name}: 통과·거부 예시가 모두 있어야 한다"
        for v in valid:
            assert schemas.problems(name, json.loads(v.read_text(encoding="utf-8"))) == [], v.name
        for i in invalid:
            assert schemas.problems(name, json.loads(i.read_text(encoding="utf-8"))), i.name


def test_5_types_equal_regenerated_types():
    with tempfile.TemporaryDirectory() as td:
        out = pathlib.Path(td) / "types"
        import boundary_contracts
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_types.py"), "--out", str(out),
                            "--contracts", str(boundary_contracts.schema_dir())], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[-500:]
        have = {p.name: p.read_text(encoding="utf-8") for p in (PKG / "types").glob("*.py")}
        want = {p.name: p.read_text(encoding="utf-8") for p in out.glob("*.py")}
        assert have == want, "types/가 schemas/에서 다시 생성한 결과와 다르다. scripts/gen_types.py로 다시 생성한다"


def test_6_every_code_file_belongs_somewhere():
    for p in PKG.rglob("*.py"):
        rel = str(p.relative_to(PKG))
        if p.name == "__init__.py" or "__pycache__" in rel:
            continue
        top = rel.split("/")[0]
        assert top in ("components", "wiring", "types") or ENTRY.search(rel), f"{rel}: components/·wiring/·types/·진입점 어디에도 속하지 않음"


def test_components_declare_schemas_that_exist():
    own = {p.stem for p in (ROOT / "schemas").glob("*.json")}
    contract = {p.name.replace(".schema.json", "") for p in __import__("boundary_contracts").schema_dir().glob("*.schema.json")}
    for p in COMPONENTS:
        src = p.read_text(encoding="utf-8")
        m_in, m_out = re.search(r'INPUT_SCHEMA = "([^"]+)"', src), re.search(r'OUTPUT_SCHEMA = "([^"]+)"', src)
        if p.name.startswith("store_") or p.name == "question_store.py":
            continue    # 저장소 컴포넌트는 연산이 여럿이라 읽기 요청 스키마(Store*Query)를 wiring 주입 검증기로 쓴다
        assert m_in and m_out, f"{p.name}: INPUT_SCHEMA/OUTPUT_SCHEMA 선언이 없다"
        for n in (m_in.group(1), m_out.group(1)):
            assert n in own or n.partition("#")[0] in contract, f"{p.name}: 없는 스키마 {n}"
