"""저작 화면 브라우저 시험: 실제 서버(waitress)를 띄우고 platform 공개 입구의 대역(로그인 정보 `/oauth2/userinfo` + 신원 헤더·게이트웨이 토큰을 붙여 전달)을 앞에 두고,
공식 Playwright 이미지(Docker)의 크로미움으로 초안 작성·자료 올리기·확정·버전 목록·판정 보기·검수 결정·신호를 눌러 화면 결과를 단언한다.
release-gate 는 대역(판정 하나)이다. docker 가 없으면 SKIP. 정리는 이 시험이 띄운 컨테이너(--rm)뿐이다."""
import http.server
import json
import os
import shutil
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from conftest import example_text
from helpers import HANG_GUARD_SECONDS, stop_http
from question_authoring.components.link_authoring_server import Api, make_server
from question_authoring.wiring.service import AuthoringService
from question_authoring.wiring.stores import SqliteQuestionStore
from test_decisions_and_versions import GATE_DECISION

DOCKER = shutil.which("docker")
IMAGE = "mcr.microsoft.com/playwright/python:v1.49.0-noble"     # 브라우저는 이미지에 있고 파이썬 패키지(버전은 이미지 태그와 같다)는 실행 때 PyPI 에서 받는다
HERE = Path(__file__).resolve().parent
WHO = {"kim": ("u-kim", "dr-kim", "author"), "park": ("u-park", "dr-park", "clinical-reviewer")}


class FakeGate:
    def find_decisions(self, *, question_set, cursor=None, limit=100, **kw):
        d = json.loads(json.dumps(GATE_DECISION))
        if question_set["id"] != "qs-ui2":
            return [], None
        d["subject"]["question_sets"][0].update(id=question_set["id"])
        return [d], None


def make_platform(qa_port, mutate):
    """platform 공개 입구의 대역: 쿠키로 사람을 고르고 userinfo 를 주며, 클라이언트의 신원 헤더는 지우고 게이트웨이 토큰·신원 헤더를 붙여 서비스로 전달한다."""
    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"      # 연결 재사용: 모든 응답에 Content-Length 가 있다. 새 연결이 적을수록 하네스의 포트 전달 경쟁(빈 응답)이 줄어든다

        def log_message(self, *a):
            if os.environ.get("PLATFORM_DEBUG"):
                print("PLATFORM", a, flush=True)

        def _who(self):
            c = self.headers.get("Cookie", "")
            for k in WHO:
                if f"who={k}" in c:
                    return WHO[k]
            return None

        def _send(self, st, body=b"", ctype="application/json", extra=None):
            self.send_response(st)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _handle(self):
            try:
                self._handle1()
            except Exception as e:      # noqa: BLE001  대역의 결함이 "빈 응답" 으로만 보이지 않게 502 와 종류를 낸다
                self._send(502, ("대역 오류 " + type(e).__name__).encode(), "text/plain")

        def _handle1(self):
            who = self._who()
            if self.path.startswith("/oauth2/userinfo"):
                if who is None:
                    return self._send(401)
                return self._send(200, json.dumps({"user": who[0], "preferredUsername": who[1], "groups": ["/" + who[2]]}).encode())
            if who is None:
                return self._send(401, json.dumps({"code": "PERMISSION_DENIED", "message": "로그인이 필요하다", "retryable": False,
                                                   "pipeline_schema_version": "3.4"}).encode())
            if mutate and self.path.startswith("/v1/set-decisions"):
                return self._send(404, json.dumps({"code": "NOT_FOUND", "message": "x", "retryable": False, "pipeline_schema_version": "3.4"}).encode())
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else None
            req = urllib.request.Request(f"http://127.0.0.1:{qa_port}{self.path}", data=body, method=self.command)
            for k in ("Content-Type", "Accept"):
                if self.headers.get(k):
                    req.add_header(k, self.headers[k])
            req.add_header("Authorization", "Bearer gw-token")
            req.add_header("X-Openit-User", who[0])
            req.add_header("X-Openit-Roles", who[2])
            try:
                r = urllib.request.urlopen(req, timeout=60)
            except urllib.error.HTTPError as e:
                r = e
            data = r.read()
            self._send(r.status, data, r.headers.get("Content-Type", "application/json"),
                       {k: v for k, v in (("Retry-After", r.headers.get("Retry-After")), ("Content-Security-Policy", r.headers.get("Content-Security-Policy"))) if v})
        do_GET = do_POST = _handle
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def stack(tmp_path):
    if DOCKER is None or subprocess.run([DOCKER, "info"], capture_output=True).returncode != 0:
        pytest.skip("docker 데몬 없음")
    if subprocess.run([DOCKER, "image", "inspect", IMAGE], capture_output=True).returncode != 0:
        pytest.skip(f"{IMAGE} 이미지가 없다(docker pull 필요)")
    svc = AuthoringService(SqliteQuestionStore(), require_two_person=True)
    api = Api(svc, {}, gateway_tokens={"gw-token"}, gate=FakeGate())
    qa = make_server(api, "127.0.0.1", 0)
    th = threading.Thread(target=qa.run, daemon=True)
    th.start()
    made = []

    def start(mutate=False):
        p = make_platform(qa.effective_port, mutate)
        made.append(p)
        return p.server_address[1]
    (tmp_path / "form.txt").write_bytes(example_text("qs-ui2"))
    shutil.copy(HERE / "browser" / "flow.py", tmp_path / "flow.py")
    for f in tmp_path.iterdir():
        f.chmod(0o644)
    tmp_path.chmod(0o755)
    yield start, tmp_path
    for p in made:
        p.shutdown(); p.server_close()
    stop_http(qa, th)


def run_flow(port, d):
    return subprocess.run([DOCKER, "run", "--rm", "--ipc=host", "--add-host", "host.docker.internal:host-gateway", "-v", f"{d}:/t:ro", IMAGE,
                           "sh", "-c", f"pip install -q --disable-pip-version-check playwright==1.49.0 >/dev/null 2>&1 && python /t/flow.py http://host.docker.internal:{port}"], capture_output=True, text=True, timeout=HANG_GUARD_SECONDS)


def test_author_and_reviewer_flow_in_a_real_browser(stack):
    start, d = stack
    r = run_flow(start(), d)
    steps = [l for l in r.stdout.splitlines() if l.startswith("STEP")]
    assert r.returncode == 0 and "DONE" in r.stdout, r.stdout[-1500:] + r.stderr[-500:]
    assert len(steps) == 9, steps


def test_browser_flow_fails_when_the_decision_lookup_is_broken(stack):
    """변이 확인: 판정 조회 경로가 404 이면 화면이 '반려' 를 보이지 못해 시나리오가 실패한다(단언이 화면 결과를 실제로 본다는 증거)."""
    start, d = stack
    r = run_flow(start(mutate=True), d)
    assert r.returncode == 1 and "FAIL" in r.stdout and "versions list and gate decision" in r.stdout.split("FAIL")[0], r.stdout[-800:]
