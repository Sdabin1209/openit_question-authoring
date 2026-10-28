"""Dockerfile 이미지 라벨 시험(계약 ImageLabels, deploy CR-D28).
이 서비스는 turn-engine·model-runtime을 쓰지 않으므로 두 라벨은 상수 `n/a`다(`none`·`unknown`은 계약이 거부).
계약 ImageLabels 스키마가 설치된 계약 패키지에 있으면 그 스키마로도 검증한다(없으면 그 부분만 건너뛴다).
기대값 출처: deploy CR-D28의 표와 ImageLabels 스키마(필수 6개, turn-engine·model-runtime은 `v<semver>` 또는 `n/a`)."""
import pathlib
import re

import pytest

DOCKERFILE = pathlib.Path(__file__).resolve().parent.parent / "Dockerfile"
SAMPLE_ARGS = {"COMMIT": "85b3552", "BOUNDARY_TAG": "v2.1.1", "SURVEY_SCHEMA_TAG": "1"}


def dockerfile_labels() -> dict:
    text = DOCKERFILE.read_text(encoding="utf-8")
    m = re.search(r"^LABEL\s+((?:.*\\\n)*.*)$", text, re.M)
    body = m.group(1).replace("\\\n", " ")
    out = {}
    for k, v in re.findall(r"(io\.openit\.[\w.-]+)=(\S+)", body):
        out[k] = re.sub(r"\$(\w+)", lambda g: SAMPLE_ARGS[g.group(1)], v)
    return out


def test_unused_neighbours_are_na_not_none():
    labels = dockerfile_labels()
    assert labels["io.openit.turn-engine"] == "n/a"
    assert labels["io.openit.model-runtime"] == "n/a"


def test_required_labels_present_and_well_formed():
    labels = dockerfile_labels()
    for k in ("io.openit.service", "io.openit.revision", "io.openit.contracts.boundary",
              "io.openit.contracts.survey-schema", "io.openit.turn-engine", "io.openit.model-runtime"):
        assert k in labels, k
    assert labels["io.openit.service"] == "question-authoring"
    assert re.fullmatch(r"v\d+\.\d+\.\d+", labels["io.openit.contracts.boundary"])
    assert not {"none", "unknown"} & set(labels.values())


def test_labels_validate_against_contract_schema_if_installed():
    jsonschema = pytest.importorskip("jsonschema")
    try:
        from boundary_contracts import load_schema
        schema = load_schema("ImageLabels")
    except Exception:
        pytest.skip("설치된 계약 패키지에 ImageLabels 스키마가 아직 없다")
    assert list(jsonschema.Draft202012Validator(schema).iter_errors(dockerfile_labels())) == []
