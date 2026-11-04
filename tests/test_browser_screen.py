"""저작 화면 브라우저 시험. 모든 것이 사용자 정의 docker 네트워크 안의 컨테이너다(호스트 포트 게시 없음, 컨테이너 이름으로 접속):
  앱 컨테이너 = 제품 이미지(scripts/build-local-image.sh 로 빌드) 안에서 서비스(waitress)와 두 대역(release-gate, platform 공개 입구)을 함께 돌린다
  브라우저 컨테이너 = 공식 Playwright 이미지, 같은 네트워크에서 앱 컨테이너의 platform 대역 주소로 접속한다
왜 호스트 포트를 거치지 않나: Docker Desktop 의 호스트 포트 전달은 부하에서 빈 응답을 낸다(표준 해법은 같은 네트워크의 컨테이너 이름 접속).
정리는 이 시험이 만든 이름의 컨테이너·네트워크·이미지 태그뿐이다. docker 가 없으면 SKIP."""
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from helpers import HANG_GUARD_SECONDS
from test_decisions_and_versions import GATE_DECISION

DOCKER = shutil.which("docker")
PW_IMAGE = "mcr.microsoft.com/playwright/python:v1.49.0-noble"     # 브라우저는 이미지에 있고 파이썬 패키지(버전은 이미지 태그와 같다)는 실행 때 PyPI 에서 받는다
ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent


def sh(*a, check=True, timeout=HANG_GUARD_SECONDS, **k):
    return subprocess.run(a, capture_output=True, text=True, check=check, timeout=timeout, **k)


@pytest.fixture(scope="module")
def app_image():
    if DOCKER is None or subprocess.run([DOCKER, "info"], capture_output=True).returncode != 0:
        pytest.skip("docker 데몬 없음")
    if subprocess.run([DOCKER, "image", "inspect", PW_IMAGE], capture_output=True).returncode != 0:
        pytest.skip(f"{PW_IMAGE} 이미지가 없다(docker pull 필요)")
    tag = f"question-authoring:browser-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    env = dict(os.environ)
    env.setdefault("GIT_CONFIG_GLOBAL", str(ROOT.parent / "head" / "shared" / "gitconfig-local-mirrors"))     # 원격 동결 중의 표준 경로(S14)
    r = subprocess.run(["bash", str(ROOT / "scripts" / "build-local-image.sh"), tag], capture_output=True, text=True, env=env, timeout=HANG_GUARD_SECONDS)
    assert r.returncode == 0, r.stderr[-800:]
    yield tag
    subprocess.run([DOCKER, "rmi", tag], capture_output=True, timeout=120)          # 이 시험이 만든 태그만


def run_flow(app_image, tmp_path, mutate):
    from conftest import example_text
    uid = uuid.uuid4().hex[:8]
    net, app = f"qa-browser-net-{uid}", f"qa-browser-app-{uid}"
    d = tmp_path
    (d / "form.txt").write_bytes(example_text("qs-ui2"))
    (d / "gate.json").write_text(json.dumps(GATE_DECISION), encoding="utf-8")
    for n in ("flow.py", "stubs.py"):
        shutil.copy(HERE / "browser" / n, d / n)
    for f in d.iterdir():
        f.chmod(0o644)
    d.chmod(0o755)
    sh(DOCKER, "network", "create", net)
    try:
        start = ("python /t/stubs.py gate 9001 /t/gate.json & "
                 f"python /t/stubs.py platform 8081 8080 {'mutate' if mutate else ''} & "
                 "exec python -m question_authoring")
        sh(DOCKER, "run", "-d", "--name", app, "--network", net, "-v", f"{d}:/t:ro", "--entrypoint", "sh",
           "-e", "QA_DEV_MODE=1", "-e", "QA_GATEWAY_TOKENS=gw-token", "-e", "QA_REQUIRE_TWO_PERSON=true",
           "-e", "QA_GATE_URL=http://127.0.0.1:9001", "-e", "QA_GATE_TOKEN=t", app_image, "-c", start)        # 이웃 대역은 같은 컨테이너의 loopback(R1.1 이 허용하는 평문)
        # 준비 판정: 앱 컨테이너의 platform 대역이 응답할 때까지(조건)
        end = time.monotonic() + 60
        while time.monotonic() < end:
            if subprocess.run([DOCKER, "exec", app, "python", "-c", "import urllib.request as u,urllib.error as e\n"
                               "try: u.urlopen('http://127.0.0.1:8081/oauth2/userinfo', timeout=2)\n"
                               "except e.HTTPError: pass"], capture_output=True).returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise AssertionError("앱 컨테이너가 준비되지 않았다: " + sh(DOCKER, "logs", "--tail", "20", app, check=False).stdout[-500:])
        return subprocess.run([DOCKER, "run", "--rm", "--ipc=host", "--network", net, "-v", f"{d}:/t:ro", PW_IMAGE, "sh", "-c",
                               f"pip install -q --disable-pip-version-check playwright==1.49.0 >/dev/null 2>&1 && python /t/flow.py http://{app}:8081"],
                              capture_output=True, text=True, timeout=HANG_GUARD_SECONDS)
    finally:
        subprocess.run([DOCKER, "rm", "-f", app], capture_output=True, timeout=60)
        subprocess.run([DOCKER, "network", "rm", net], capture_output=True, timeout=60)


def test_author_and_reviewer_flow_in_a_real_browser(app_image, tmp_path):
    r = run_flow(app_image, tmp_path, mutate=False)
    steps = [l for l in r.stdout.splitlines() if l.startswith("STEP")]
    assert r.returncode == 0 and "DONE" in r.stdout, r.stdout[-1500:] + r.stderr[-500:]
    assert len(steps) == 9, steps


def test_browser_flow_fails_when_the_decision_lookup_is_broken(app_image, tmp_path):
    """변이 확인: 판정 조회 경로가 404 이면 화면이 '반려' 를 보이지 못해 시나리오가 실패한다(단언이 화면 결과를 실제로 본다는 증거)."""
    r = run_flow(app_image, tmp_path, mutate=True)
    assert r.returncode == 1 and "FAIL" in r.stdout and "versions list and gate decision" in r.stdout.split("FAIL")[0], r.stdout[-800:]
