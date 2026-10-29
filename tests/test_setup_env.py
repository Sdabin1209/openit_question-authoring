"""scripts/setup-env.sh의 결과 보고(계약 SetupEnvReport, boundary-contracts 2.6.0, U43): 표준 출력의 마지막 줄이 계약을 통과하고
environment.test_command가 리포 루트에서 이 영역 시험을 돌린다. 설치(pip)는 가짜 .venv의 python으로 대신해 네트워크 없이 본다.
진짜 설치까지의 확인은 깨끗한 clone에서 setup-env.sh를 직접 돌려 한다(보고서에 출력)."""
import json
import os
import pathlib
import shutil
import subprocess
import sys

import boundary_contracts as bc
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHIM = '''#!/usr/bin/env bash
# 가짜 .venv/bin/python: `-m pip ...`는 표준 출력에 잡음을 내고 성공(또는 FAKE_PIP_FAIL이면 실패), 나머지는 진짜 python으로 넘긴다
if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then echo "Collecting noise on stdout"; [ -n "${FAKE_PIP_FAIL:-}" ] && exit 3; exit 0; fi
exec "__REAL__" "$@"
'''


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "setup-env.sh", r / "scripts" / "setup-env.sh")
    (r / "requirements.lock").write_text("")
    (r / ".venv" / "bin").mkdir(parents=True)
    py = r / ".venv" / "bin" / "python"
    py.write_text(SHIM.replace("__REAL__", sys.executable))
    py.chmod(0o755)
    return r


def run(repo, **env):
    return subprocess.run(["bash", str(repo / "scripts" / "setup-env.sh")], cwd="/", capture_output=True, text=True, timeout=60,
                          env=dict(os.environ, **env))


def test_last_stdout_line_is_a_valid_report_even_with_noise(repo):
    p = run(repo)
    assert p.returncode == 0, p.stderr
    lines = p.stdout.strip().splitlines()
    rep = json.loads(lines[-1])
    assert not list(bc.validator("SetupEnvReport").iter_errors(rep)), rep
    assert "noise" not in p.stdout and "noise" in p.stderr              # 진행 기록은 표준 오류, 표준 출력에는 보고뿐
    assert rep["area"] == "question-authoring" and rep["status"] == "ok" and rep["exit_code"] == 0
    assert rep["environment"] == {"kind": "python-venv", "path": ".venv", "test_command": [".venv/bin/python", "-m", "pytest", "-q"]}


def test_failure_still_reports_a_valid_line_and_exits_nonzero(repo):
    p = run(repo, FAKE_PIP_FAIL="1")
    assert p.returncode != 0
    rep = json.loads(p.stdout.strip().splitlines()[-1])
    assert not list(bc.validator("SetupEnvReport").iter_errors(rep)), rep
    assert rep["status"] == "fail" and rep["exit_code"] == p.returncode and "environment" not in rep


def test_rerun_is_idempotent(repo):
    a, b = run(repo), run(repo)
    ra, rb = (json.loads(x.stdout.strip().splitlines()[-1]) for x in (a, b))
    assert ra["environment"] == rb["environment"] and ra["status"] == rb["status"] == "ok"


def test_test_command_runs_this_areas_tests_from_the_repo_root(repo):
    """선언한 test_command가 리포 루트에서 이 영역 시험을 돌린다(전체 실행은 재귀라 수집만 한다. 깨끗한 clone에서 전체를 돌려 보고한다)."""
    cmd = json.loads(run(repo).stdout.strip().splitlines()[-1])["environment"]["test_command"]
    assert cmd[:3] == [".venv/bin/python", "-m", "pytest"]
    real = [sys.executable] + cmd[1:] + ["--collect-only", "tests/test_build_image.py"]    # 이 시험 환경의 python으로 같은 인자를 준다
    p = subprocess.run(real, cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0 and "test_build_image.py" in p.stdout
