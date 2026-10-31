"""영역 빌드 진입점 scripts/build-image.sh (계약 BuildImageRequest/BuildImageResult, boundary-contracts 2.5.0, U42, ADR 0019).
기대값 출처: 계약 스키마 설명(요청 검증, 쓰는 키가 없으면 실패, 결과는 표준 출력 마지막 줄, 라벨은 이미지에 붙은 io.openit.*).
실제 docker 빌드는 scripts/smoke.sh가 한 번 한다. 여기서는 가짜 DOCKER로 인자와 결과 모양을 본다."""
from helpers import HANG_GUARD_SECONDS
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import boundary_contracts as bc
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build-image.sh"
SHA = "a" * 64
# 네트워크 없이 wheel을 만든다: 현재 환경에 setuptools와 wheel이 있을 때만 격리 빌드를 끈다(없으면 pip 기본, 색인 접근 필요)
OFFLINE_WHEEL = {"PIP_NO_BUILD_ISOLATION": "0"} if all(importlib.util.find_spec(m) for m in ("setuptools", "wheel")) else {}


def make_tree(path, name, version="1.0"):
    """wheel로 만들 수 있는 가짜 라이브러리 소스 트리."""
    path.mkdir(parents=True)
    (path / "pyproject.toml").write_text(
        f'[build-system]\nrequires=["setuptools"]\nbuild-backend="setuptools.build_meta"\n[project]\nname="{name}"\nversion="{version}"\n'
        f'[tool.setuptools]\npackages=["{name}"]\n')
    (path / name).mkdir()
    (path / name / "__init__.py").write_text("")
    return str(path)


LOCK = ("boundary-contracts @ git+ssh://git@github.com/x/openit_boundary-contracts.git@v3.1.2\n"
        "openit-survey-schema @ git+ssh://git@github.com/x/openit_survey-service.git@survey-schema-v3.0.0#subdirectory=schema\n"
        "openit-link @ git+ssh://git@github.com/x/openit_link.git@v0.4.3\n")


def write_lock(src, text=LOCK):
    (pathlib.Path(src) / "requirements.lock").write_text(text)


@pytest.fixture
def req(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "Dockerfile").write_text("FROM scratch\n")
    (src / "deploy-exposure.json").write_text((ROOT / "deploy-exposure.json").read_text(encoding="utf-8"))
    (src / "deploy-jobs.json").write_text((ROOT / "deploy-jobs.json").read_text(encoding="utf-8"))
    (src / ".git").mkdir()
    (src / ".git" / "HEAD").write_text("x")
    (src / ".venv").mkdir()
    (src / "question_authoring").mkdir()
    (src / "question_authoring" / "m.py").write_text("")
    write_lock(src)
    # 실제 패키지와 같은 이름·버전의 가짜 트리: 라벨은 요청이 아니라 이 wheel의 버전에서 나온다(U50)
    return {"source_dir": str(src), "revision": "abcdef1", "versions": {"boundary": "v3.1.2", "survey_schema": "v3.0.0"},
            "dependency_sources": {"boundary": make_tree(tmp_path / "bc", "boundary_contracts", "3.1.2"),
                                   "survey_schema": make_tree(tmp_path / "ss", "openit_survey_schema", "3.0.0")},
            "image_tag": "question-authoring-test/q:t1"}


def run(tmp_path, request, docker="/bin/false"):
    f = tmp_path / "req.json"
    f.write_text(json.dumps(request))
    lk = tmp_path / "lk"
    if not lk.exists():
        make_tree(lk, "openit_link", "0.1.0")
    env = dict(os.environ, DOCKER=docker, PYTHON=sys.executable, OPENIT_LINK_SPEC=str(lk), **OFFLINE_WHEEL)
    return subprocess.run(["bash", str(SCRIPT), str(f)], env=env, capture_output=True, text=True, timeout=HANG_GUARD_SECONDS)


def bad(request, **kw):
    r = json.loads(json.dumps(request))
    for k, v in kw.items():
        if v is None:
            r.pop(k, None)
        else:
            r[k] = v
    return r


def test_unknown_key_is_rejected_before_docker(tmp_path, req):
    p = run(tmp_path, dict(req, surprise=1))
    assert p.returncode == 2 and "BuildImageRequest" in p.stderr


def test_bad_values_are_rejected(tmp_path, req):
    for r in (dict(req, revision="not-hex"), dict(req, image_tag="Bad Tag"), dict(req, source_dir="relative/path"),
              bad(req, versions={"survey_schema": "v3.0.0"})):
        assert run(tmp_path, r).returncode == 2


@pytest.mark.parametrize("path", [("versions", "survey_schema"), ("dependency_sources", "survey_schema"), ("dependency_sources", "boundary")])
def test_missing_key_this_area_uses_fails_without_default(tmp_path, req, path):
    r = json.loads(json.dumps(req))
    del r[path[0]][path[1]]
    p = run(tmp_path, r)
    assert p.returncode == 2 and path[1] in p.stderr


def test_variant_is_rejected_because_this_repo_has_one_image(tmp_path, req):
    p = run(tmp_path, dict(req, variant="gpu"))
    assert p.returncode == 2 and "variant" in p.stderr


def test_missing_request_file_fails(tmp_path):
    p = subprocess.run(["bash", str(SCRIPT), str(tmp_path / "none.json")], capture_output=True, text=True,
                       env=dict(os.environ, PYTHON=sys.executable))
    assert p.returncode == 2


FAKE_DOCKER = r'''#!/usr/bin/env bash
# 가짜 docker: build 인자를 기록하고, inspect는 Dockerfile이 붙일 라벨을 인자에서 만들어 돌려준다
log="$FAKE_LOG"
case "$1" in
  build) shift; echo "build $*" >> "$log"; ctx="${@: -1}"; ls -A "$ctx" >> "$log"; ls "$ctx/build/wheels" >> "$log" ;;
  image) case "$4" in
      '{{.Id}}') echo "sha256:__SHA__" ;;
      *) c=$(grep -o -- '--build-arg COMMIT=[^ ]*' "$log" | cut -d= -f2); b=$(grep -o -- 'BOUNDARY_TAG=[^ ]*' "$log" | cut -d= -f2); s=$(grep -o -- 'SURVEY_SCHEMA_TAG=[^ ]*' "$log" | cut -d= -f2)
         echo "{\"io.openit.service\":\"question-authoring\",\"io.openit.revision\":\"$c\",\"io.openit.contracts.boundary\":\"$b\",\"io.openit.contracts.survey-schema\":\"$s\",\"io.openit.model-runtime\":\"n/a\",\"org.other\":\"x\"}" ;;
    esac ;;
esac
'''


def test_result_last_line_matches_contract_and_labels_match_request(tmp_path, req):
    fake = tmp_path / "docker"
    fake.write_text(FAKE_DOCKER.replace("__SHA__", SHA))
    fake.chmod(0o755)
    log = tmp_path / "log.txt"
    os.environ["FAKE_LOG"] = str(log)
    try:
        p = run(tmp_path, req, docker=str(fake))
    finally:
        del os.environ["FAKE_LOG"]
    assert p.returncode == 0, p.stderr
    last = p.stdout.strip().splitlines()[-1]
    out = json.loads(last)
    assert not list(bc.validator("BuildImageResult").iter_errors(out))
    assert out["image"] == req["image_tag"] and out["image_id"] == "sha256:" + SHA
    assert out["labels"]["io.openit.revision"] == req["revision"]
    assert out["labels"]["io.openit.contracts.boundary"] == "v3.1.2"
    assert out["labels"]["io.openit.contracts.survey-schema"] == "3"          # v3.0.0의 주 버전
    assert "io.openit.turn-engine" not in out["labels"]                       # 3.0.0에서 라벨이 없어졌다
    assert "org.other" not in out["labels"]
    rec = log.read_text()
    assert "-t " + req["image_tag"] in rec
    assert ".git" not in rec.split("build ")[1].splitlines()[1:6] and ".venv" not in rec     # 컨텍스트에 .git, .venv가 없다
    assert rec.count(".whl") == 3                                             # 계약·문진 스키마·통신 라이브러리 wheel
    assert not (pathlib.Path(req["source_dir"]) / "build").exists()           # source_dir은 고치지 않는다


def test_same_request_gives_same_wheels(tmp_path, req):
    """멱등의 한 조건: 같은 소스와 시각이면 wheel이 같다(SOURCE_DATE_EPOCH)."""
    import hashlib, zipfile  # noqa: E401
    def digest():
        w = tmp_path / f"w{len(list(tmp_path.glob('w*')))}"
        w.mkdir()
        t = tmp_path / "t"
        subprocess.run([sys.executable, "-m", "pip", "wheel", "-q", "--no-deps", "-w", str(w), req["dependency_sources"]["boundary"]],
                       env=dict(os.environ, SOURCE_DATE_EPOCH="0", **OFFLINE_WHEEL), check=True)
        return hashlib.sha256(next(w.glob("*.whl")).read_bytes()).hexdigest()
    assert digest() == digest()


def test_default_interpreter_is_the_repo_venv_and_failures_stop_early(tmp_path, req):
    """PYTHON을 주지 않으면 리포의 .venv를 쓰고, 없으면 분명한 메시지로 멈춘다. 요청 해석 실패가 다음 줄의 엉뚱한 오류로 번지지 않는다."""
    f = tmp_path / "req.json"
    f.write_text(json.dumps(dict(req, surprise=1)))
    env = {k: v for k, v in os.environ.items() if k != "PYTHON"}
    p = subprocess.run(["bash", str(SCRIPT), str(f)], env=env, capture_output=True, text=True, timeout=HANG_GUARD_SECONDS)
    assert p.returncode == 2 and "unbound" not in p.stderr and "tar:" not in p.stderr
    venv = ROOT / ".venv" / "bin" / "python"
    if venv.exists():
        assert "BuildImageRequest" in p.stderr        # .venv에 계약 패키지가 있어 요청 검증 메시지까지 간다
    else:
        assert "해석기를 찾지 못했다" in p.stderr


def test_missing_exposure_declaration_fails(tmp_path, req):
    (pathlib.Path(req["source_dir"]) / "deploy-exposure.json").unlink()
    p = run(tmp_path, req)
    assert p.returncode == 2 and "deploy-exposure.json" in p.stderr


def test_label_values_come_from_the_installed_wheels_not_the_request(tmp_path, req):
    """U50: 요청의 버전 문자열이 아니라 wheel(실제 설치될 것)의 버전에서 라벨 값을 찍는다. 같은 요청이라도 트리가 다르면 요청이 거짓이 된다."""
    r = json.loads(json.dumps(req))
    r["versions"]["boundary"] = "v3.1.3"          # 요청은 3.1.3이라 하지만 트리는 3.1.2
    p = run(tmp_path, r)
    assert p.returncode == 2 and "v3.1.2" in p.stderr and "v3.1.3" in p.stderr and "라벨" in p.stderr


def test_survey_schema_mismatch_between_request_and_tree_fails(tmp_path, req):
    r = json.loads(json.dumps(req))
    r["versions"]["survey_schema"] = "v1.3.0"
    p = run(tmp_path, r)
    assert p.returncode == 2 and "3.0.0" in p.stderr and "v1.3.0" in p.stderr


@pytest.mark.parametrize("lock", [LOCK.replace("@v3.1.2", "@v2.9.0"), LOCK.replace("survey-schema-v3.0.0", "survey-schema-v1.1.0"), ""])
def test_request_that_differs_from_this_repos_pin_fails(tmp_path, req, lock):
    """요청 버전이 이 영역의 고정(requirements.lock)과 다르면 라벨을 찍지 않고 실패한다(라벨이 거짓이 되지 않게)."""
    write_lock(req["source_dir"], lock)
    p = run(tmp_path, req)
    assert p.returncode == 2 and "requirements.lock" in p.stderr


def test_docker_gets_the_installed_versions(tmp_path, req):
    fake = tmp_path / "docker"
    fake.write_text(FAKE_DOCKER.replace("__SHA__", SHA))
    fake.chmod(0o755)
    log = tmp_path / "log.txt"
    os.environ["FAKE_LOG"] = str(log)
    try:
        assert run(tmp_path, req, docker=str(fake)).returncode == 0
    finally:
        del os.environ["FAKE_LOG"]
    rec = log.read_text()
    assert "BOUNDARY_TAG=v3.1.2" in rec and "SURVEY_SCHEMA_TAG=3 " in rec
