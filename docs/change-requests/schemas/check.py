"""CR-0007의 스키마 초안을 예시 문서로 검증한다. 실행: .venv/bin/python docs/change-requests/schemas/check.py
examples/<대상>.valid*.json 은 통과해야 하고 <대상>.invalid.json 은 거부되어야 한다. 대상은 스키마 파일 이름 또는 $defs 이름."""
import json, pathlib, sys
import jsonschema
here = pathlib.Path(__file__).parent
# (예시 이름 앞부분 -> (스키마 파일, $defs 이름 또는 None))
TARGETS = {}
for f in sorted(here.glob("*.schema.json")):
    s = json.loads(f.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(s)
    for name in s.get("$defs", {}):
        TARGETS[name] = (f, name)
TARGETS["signals"] = (here / "signals-response.schema.json", None)
bad = 0
for ex in sorted((here / "examples").glob("*.json")):
    stem = ex.name[:-5]
    target, _, kind = stem.partition(".")
    kind = "invalid" if stem.endswith(".invalid") else "valid"
    if stem.endswith(".invalid"):
        target = stem[: -len(".invalid")]
    f, d = TARGETS[target.split(".")[0]]
    schema = json.loads(f.read_text(encoding="utf-8"))
    if d:
        schema = {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{d}"}
    errs = list(jsonschema.Draft202012Validator(schema).iter_errors(json.loads(ex.read_text(encoding="utf-8"))))
    ok = (not errs) if kind == "valid" else bool(errs)
    bad += not ok
    print(("OK  " if ok else "FAIL"), ex.name, "-" if not errs else "거부: " + errs[0].message[:70])
print(f"예시 {len(list((here/'examples').glob('*.json')))}개, 기대와 다른 것 {bad}개")
sys.exit(1 if bad else 0)
