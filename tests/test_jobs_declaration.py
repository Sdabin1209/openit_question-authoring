"""잡 선언(계약 JobsDeclaration, 이미지 라벨 io.openit.jobs): 정본 파일 deploy-jobs.json이 계약을 통과하고, 실제 purge 잡과 맞고,
build-image.sh가 내보내기 전에 계약으로 검증해 라벨로 단다(R7)."""
import json
import os
import pathlib
import re
import subprocess
import sys

import boundary_contracts as bc

from helpers import HANG_GUARD_SECONDS
from test_build_image import FAKE_DOCKER, OFFLINE_WHEEL, SHA, make_tree, write_lock

ROOT = pathlib.Path(__file__).resolve().parent.parent
DECL = json.loads((ROOT / "deploy-jobs.json").read_text(encoding="utf-8"))


def test_declaration_passes_the_contract():
    assert list(bc.validator("JobsDeclaration").iter_errors(DECL)) == []


def test_declared_purge_job_is_the_real_command_and_idempotent():
    (job,) = DECL["jobs"]
    assert job["name"] == "purge" and job["argv"] == ["python", "-m", "question_authoring", "purge"] and job["idempotent"] is True
    import inspect
    from question_authoring import __main__ as qa_main
    assert 'argv[:1] == ["purge"]' in inspect.getsource(qa_main.main)      # 진입점이 purge 서브커맨드를 안다


def test_declared_env_names_are_read_by_the_job():
    """선언한 환경 변수(비밀 제외)와 비밀 파일 이름이 진입점 소스에 실제로 있다."""
    src = (ROOT / "question_authoring" / "__main__.py").read_text(encoding="utf-8")
    (job,) = DECL["jobs"]
    for name in job["env"]:
        assert name in src, name
    assert "QA_DATABASE_URL" in src and job["secret_files"] == ["QA_DATABASE_URL_FILE"]


def _build(tmp_path, jobs_text):
    src = tmp_path / "src"
    src.mkdir()
    (src / "Dockerfile").write_text("FROM scratch\n")
    (src / "deploy-exposure.json").write_text((ROOT / "deploy-exposure.json").read_text(encoding="utf-8"))
    if jobs_text is not None:
        (src / "deploy-jobs.json").write_text(jobs_text)
    write_lock(src)
    req = {"source_dir": str(src), "revision": "abcdef1", "versions": {"boundary": "v3.1.2", "survey_schema": "v3.0.0"},
           "dependency_sources": {"boundary": make_tree(tmp_path / "bc", "boundary_contracts", "3.1.2"),
                                  "survey_schema": make_tree(tmp_path / "ss", "openit_survey_schema", "3.0.0")},
           "image_tag": "question-authoring-test/q:t1"}
    fake = tmp_path / "docker"
    fake.write_text(FAKE_DOCKER.replace("__SHA__", SHA))
    fake.chmod(0o755)
    (tmp_path / "req.json").write_text(json.dumps(req))
    log = tmp_path / "log.txt"
    p = subprocess.run(["bash", str(ROOT / "scripts" / "build-image.sh"), str(tmp_path / "req.json")], capture_output=True, text=True,
                       env=dict(os.environ, DOCKER=str(fake), PYTHON=sys.executable, FAKE_LOG=str(log),
                                OPENIT_LINK_SPEC=make_tree(tmp_path / "lk", "openit_link", "0.1.0"), **OFFLINE_WHEEL), timeout=HANG_GUARD_SECONDS)
    return p, log


def test_build_attaches_the_declaration_as_label(tmp_path):
    p, log = _build(tmp_path, (ROOT / "deploy-jobs.json").read_text(encoding="utf-8"))
    assert p.returncode == 0, p.stderr
    m = re.search(r"--label io\.openit\.jobs=(\{.*?\}) --", log.read_text())
    assert m and json.loads(m.group(1)) == DECL


def test_build_refuses_a_declaration_that_breaks_the_contract(tmp_path):
    bad = json.dumps({"jobs": [{"name": "purge", "argv": ["python"], "idempotent": False}]})   # idempotent는 true만
    p, log = _build(tmp_path, bad)
    assert p.returncode == 2 and "JobsDeclaration" in p.stderr and not log.exists()          # 라벨을 달지 않고 docker를 부르지 않았다


def test_build_refuses_when_the_declaration_is_missing(tmp_path):
    p, log = _build(tmp_path, None)
    assert p.returncode == 2 and "deploy-jobs.json" in p.stderr
