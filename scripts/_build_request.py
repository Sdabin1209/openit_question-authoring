"""build-image.sh의 요청 검사. 요청 JSON을 계약 BuildImageRequest로 검증하고, 이 영역이 쓰는 키가 있는지 확인한 뒤 셸 대입문을 낸다.
왜 따로: 셸에서 JSON을 파싱하지 않고 계약 validator 하나로 경계 입력을 검사한다(ADR 0019). 오류는 표준 오류, 종료 코드 2."""
import json
import shlex
import sys

import boundary_contracts

# 이 영역이 쓰는 키. 요청에 없으면 기본값을 두지 않고 실패한다(계약: 기준표와 어긋나지 않게)
NEEDS_VERSIONS = ("boundary", "survey_schema")
NEEDS_SOURCES = ("boundary", "survey_schema")
VARIANTS = ()    # 이 리포는 이미지가 하나다. variant를 주면 실패한다


def fail(msg):
    print(f"build-image: {msg}", file=sys.stderr)
    sys.exit(2)


def main(path):
    try:
        with open(path, encoding="utf-8") as f:
            req = json.load(f)
    except (OSError, ValueError) as e:
        fail(f"요청 파일을 읽지 못했다: {type(e).__name__}")
    errs = sorted(boundary_contracts.validator("BuildImageRequest").iter_errors(req), key=lambda e: list(map(str, e.absolute_path)))
    if errs:
        fail("요청이 BuildImageRequest를 어긴다: /" + "/".join(map(str, errs[0].absolute_path)) + f" ({errs[0].validator})")
    for k in NEEDS_VERSIONS:
        if k not in req["versions"]:
            fail(f"versions.{k}가 없다(이 영역이 쓰는 값)")
    for k in NEEDS_SOURCES:
        if k not in req.get("dependency_sources", {}):
            fail(f"dependency_sources.{k}가 없다(이 영역이 쓰는 라이브러리)")
    if req.get("variant") not in VARIANTS + (None,):
        fail(f"모르는 variant: {req['variant']}")
    v = req["versions"]
    major = v["survey_schema"][1:].split(".")[0]   # 라벨 io.openit.contracts.survey-schema는 문진 스키마 주 버전(정수, 계약 ImageLabels)
    for name, val in (("SRC", req["source_dir"]), ("REVISION", req["revision"]), ("BOUNDARY_VERSION", v["boundary"]),
                      ("SURVEY_MAJOR", major), ("SURVEY_VERSION", v["survey_schema"]), ("IMAGE_TAG", req["image_tag"]),
                      ("BOUNDARY_SRC", req["dependency_sources"]["boundary"]),
                      ("SURVEY_SRC", req["dependency_sources"]["survey_schema"])):
        print(f"{name}={shlex.quote(val)}")


if __name__ == "__main__":
    main(sys.argv[1])
