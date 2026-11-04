"""Dockerfile 이미지 라벨 시험(계약 ImageLabels, deploy CR-D28).
이 서비스는 model-runtime을 쓰지 않으므로 그 라벨은 상수 `n/a`다(`none`·`unknown`은 계약이 거부). turn-engine 라벨은 계약 3.0.0에서 없어졌고 ImageLabels가 거부한다.
계약 ImageLabels 스키마가 설치된 계약 패키지에 있으면 그 스키마로도 검증한다(없으면 그 부분만 건너뛴다).
기대값 출처: deploy CR-D28의 표와 ImageLabels 스키마(필수 4개, model-runtime은 `v<semver>` 또는 `n/a`)."""
import pathlib
import re

import pytest

DOCKERFILE = pathlib.Path(__file__).resolve().parent.parent / "Dockerfile"
SAMPLE_ARGS = {"COMMIT": "85b3552", "BOUNDARY_TAG": "v3.1.2", "SURVEY_SCHEMA_TAG": "3"}


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
    assert labels["io.openit.model-runtime"] == "n/a"
    assert "io.openit.turn-engine" not in labels      # 3.0.0에서 제거


def test_required_labels_present_and_well_formed():
    labels = dockerfile_labels()
    for k in ("io.openit.service", "io.openit.revision", "io.openit.contracts.boundary",
              "io.openit.contracts.survey-schema", "io.openit.model-runtime"):
        assert k in labels, k
    assert labels["io.openit.service"] == "question-authoring"
    assert re.fullmatch(r"v\d+\.\d+\.\d+", labels["io.openit.contracts.boundary"])
    assert not {"none", "unknown"} & set(labels.values())


def test_labels_validate_against_contract_schema():
    """계약 v2.2.2부터 ImageLabels가 패키지에 있다. 없으면 시험이 실패해야 한다(건너뛰지 않는다)."""
    from boundary_contracts import validator
    assert list(validator("ImageLabels").iter_errors(dockerfile_labels())) == []


BASE_IMAGE_ARGS = {"PYTHON_IMAGE"}                       # 기반 이미지 ARG(라벨 값이 아니다). 이름 목록을 명시해 예외로 한다
BASE_IMAGE_SHAPE = re.compile(r"[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}")      # <이미지>:<태그>@sha256:<64 hex>: digest 고정(공통 규칙 '빌드 때 고정')


def check_arg_defaults(text):
    defaults = dict(re.findall(r"^ARG (\w+)=(\S+)$", text, re.M))
    labels = {k: v for k, v in defaults.items() if k not in BASE_IMAGE_ARGS}
    assert set(labels) == {"COMMIT", "BOUNDARY_TAG", "SURVEY_SCHEMA_TAG"}
    assert set(labels.values()) == {"n/a"}
    for k in BASE_IMAGE_ARGS:
        assert BASE_IMAGE_SHAPE.fullmatch(defaults.get(k, "")), (k, defaults.get(k))


def test_arg_defaults_are_na_not_none_unknown_unset():
    """라벨용 ARG 기본값은 계약이 값 없음으로 인정하는 `n/a`뿐이다(`none`·`unknown`·`unset` 불가). 실제 값은 빌드가 넘긴다.
    기반 이미지 ARG 는 라벨이 아니므로 예외이되 digest 로 고정된 모양이어야 한다."""
    check_arg_defaults(DOCKERFILE.read_text(encoding="utf-8"))


def test_mutation_base_image_without_digest_is_rejected():
    text = DOCKERFILE.read_text(encoding="utf-8")
    bad = re.sub(r"(ARG PYTHON_IMAGE=[^@\s]+)@sha256:[0-9a-f]{64}", r"\1", text)
    assert bad != text
    with pytest.raises(AssertionError):
        check_arg_defaults(bad)


def test_bad_labels_are_rejected_by_contract():
    from boundary_contracts import validator
    bad = dict(dockerfile_labels(), **{"io.openit.model-runtime": "none"})
    assert list(validator("ImageLabels").iter_errors(bad))


def test_contract_rejects_the_removed_turn_engine_label():
    from boundary_contracts import validator
    assert list(validator("ImageLabels").iter_errors(dict(dockerfile_labels(), **{"io.openit.turn-engine": "n/a"})))
