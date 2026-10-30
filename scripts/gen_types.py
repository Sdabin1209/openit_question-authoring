#!/usr/bin/env python3
"""schemas/*.json -> question_authoring/types/*.py (datamodel-code-generator, TypedDict). 손으로 고치지 않는다.

사용: <생성기 venv>/bin/python scripts/gen_types.py [--out DIR] [--contracts DIR]
왜 계약 참조를 대체하나: 영역 사이 타입은 복사하지 않는다(DESIGN 3절). 생성 때만 계약 `$ref`를 같은 JSON 타입의 자리표시자로 바꿔 계약 타입이 types/에 복사되지 않게 한다.
검증은 실제 스키마(계약 포함)로 하고, types/는 모양 힌트일 뿐이다. 도구 버전은 requirements-gen.lock.
"""
import argparse, json, pathlib, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]


def resolve(contracts: pathlib.Path, ref: str):
    fname, _, ptr = ref.partition("#")
    doc = json.loads((contracts / fname).read_text(encoding="utf-8"))
    for part in [p for p in ptr.split("/") if p]:
        doc = doc[part]
    return doc


def stub(node, contracts):
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ".schema.json" in ref:
            t = resolve(contracts, ref).get("type")
            return {"type": t if isinstance(t, str) else "object", "description": "계약 " + ref}
        return {k: stub(v, contracts) for k, v in node.items()}
    if isinstance(node, list):
        return [stub(v, contracts) for v in node]
    return node


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "question_authoring" / "types"))
    ap.add_argument("--contracts", required=True, help="boundary_contracts 패키지의 schemas 디렉터리")
    a = ap.parse_args()
    contracts = pathlib.Path(a.contracts)
    with tempfile.TemporaryDirectory() as td:
        src = pathlib.Path(td) / "in"
        src.mkdir()
        for p in sorted((ROOT / "schemas").glob("*.json")):
            doc = stub(json.loads(p.read_text(encoding="utf-8")), contracts)
            doc.pop("$id", None)
            (src / p.name).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        out = pathlib.Path(a.out)
        if out.exists():
            shutil.rmtree(out)
        subprocess.run([sys.executable, "-m", "datamodel_code_generator", "--input", str(src), "--input-file-type", "jsonschema",
                        "--output", str(out), "--output-model-type", "typing.TypedDict", "--target-python-version", "3.10",
                        "--use-schema-description", "--disable-timestamp", "--use-standard-collections", "--use-union-operator"], check=True)


if __name__ == "__main__":
    main()
