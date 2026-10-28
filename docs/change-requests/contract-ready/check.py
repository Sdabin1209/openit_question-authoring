"""contract-ready 스키마·예시 자체 검증. 실행: .venv/bin/python docs/change-requests/contract-ready/check.py [계약 패키지 schemas 경로]
- MANIFEST.json의 examples 항목마다 def(없으면 파일 전체)로 검증한다. valid는 통과, invalid는 거부되어야 한다.
- 계약의 _common.schema.json, ReviewRecord.schema.json은 계약 패키지(boundary_contracts/schemas)에서 읽는다(읽기 전용).
- MANIFEST에 없는 예시 파일이나 예시 없는 항목이 있으면 실패한다."""
import json, pathlib, sys
import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

here = pathlib.Path(__file__).parent
if len(sys.argv) > 1:
    contract = pathlib.Path(sys.argv[1])
else:
    import boundary_contracts
    contract = pathlib.Path(boundary_contracts.__file__).parent / "schemas"
reg = Registry()
for f in list(contract.glob("*.schema.json")) + list((here / "schemas").glob("*.schema.json")):
    reg = reg.with_resource(f.name, Resource.from_contents(json.loads(f.read_text(encoding="utf-8")), DRAFT202012))
manifest = json.loads((here / "MANIFEST.json").read_text(encoding="utf-8"))
ok_n = rej_n = bad = 0
listed = set()
for item in manifest["items"]:
    sch = json.loads((here / "schemas" / f"{item['name']}.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(sch)
    assert sch["$id"] == f"{item['name']}.schema.json" and sch["title"] == item["name"], item["name"]
    kinds = set()
    for ex in item["examples"]:
        listed.add(ex["file"])
        doc = json.loads((here / "examples" / ex["file"]).read_text(encoding="utf-8"))
        target = {"$ref": f"{item['name']}.schema.json" + (f"#/$defs/{ex['def']}" if ex.get("def") else "")}
        errs = list(jsonschema.Draft202012Validator(target, registry=reg).iter_errors(doc))
        valid = ex["file"].startswith("valid/")
        good = (not errs) if valid else bool(errs)
        ok_n += good and valid; rej_n += good and not valid; bad += not good
        kinds.add((ex.get("def"), valid))
        print(("OK  " if good else "FAIL"), ex["file"], "통과" if valid and good else ("거부: " + errs[0].message[:60] if errs else "거부되어야 하는데 통과"))
    for d in (sch.get("$defs") or {}):
        if d in {e["def"] for e in item["examples"] if e.get("def")} and (d, False) not in kinds:
            print("FAIL", item["name"], d, "거부 예시 없음"); bad += 1
files = {f"{k}/{p.name}" for k in ("valid", "invalid") for p in (here / "examples" / k).glob("*.json")}
if files != listed:
    print("FAIL MANIFEST와 예시 파일 불일치:", sorted(files ^ listed)); bad += 1
print(f"통과 예시 {ok_n}건 통과, 거부 예시 {rej_n}건 거부, 기대와 다른 것 {bad}건")
sys.exit(1 if bad else 0)
