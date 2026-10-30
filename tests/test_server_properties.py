"""HTTP 서버 구현을 표준 서버(waitress)로 바꿔도 지켜야 하는 성질(단위 4a, D111).
느린 클라이언트가 다른 요청을 막지 않는다 / 멈춘 업로드는 정해진 시간에 끊긴다 /
종료 신호에 진행 중 요청을 마치고 유예 시간이 지나면 포기한다 / 요청 수가 한도를 넘으면 기다린다."""
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from helpers import start_server
from question_authoring.components.link_authoring_server import Api
from question_authoring.wiring.service import Actor

AUTH = {"Authorization": "Bearer t"}


class SlowI7:
    """ops-feedback 대역: 목록 요청을 받으면 `entered`를 켜고 `release`가 켜질 때까지(최대 hold초) 붙잡았다가 빈 목록으로 답한다.
    왜 고정 지연이 아닌가: 고정 지연은 부하에서 '요청이 이미 도착했다'·'아직 끝나지 않았다'를 보장하지 못한다. 이벤트로 순서를 정한다."""
    def __init__(self, hold=60.0):
        self.entered, self.release = threading.Event(), threading.Event()
        entered, release = self.entered, self.release

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                entered.set()
                release.wait(hold)
                d = json.dumps({"items": [], "next_cursor": None}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(d))); self.end_headers()
                try: self.wfile.write(d)
                except OSError: pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H); self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.release.set()
        self.srv.shutdown(); self.srv.server_close()


def get(port, path, headers=AUTH, timeout=10):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_stalled_client_does_not_block_other_requests(svc):
    api = Api(svc, {"t": Actor("dr-kim", "question-author")})
    port, stop = start_server(api, request_timeout=30)
    try:
        stalled = socket.create_connection(("127.0.0.1", port), timeout=5)
        stalled.sendall(b"POST /v1/drafts HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer t\r\nContent-Length: 100\r\n\r\n{")
        # 판정은 걸린 시간이 아니라 '멈춘 연결이 살아 있는 동안(서버 무응답 상한 30초) 다른 요청이 응답을 받았다'이다.
        # 막혔다면 이 요청은 30초 뒤에야 끊기므로 읽기 상한 10초(정상은 수 밀리초, 여유 1000배 이상)에서 실패한다
        assert get(port, "/v1/drafts", timeout=10) == 200
        stalled.close()
    finally:
        stop()


def test_requests_over_the_limit_wait_instead_of_failing(svc):
    """처리 스레드가 1개일 때 두 번째 요청은 거절(이전: 503)되지 않고 첫 요청이 끝나기를 기다렸다가 정상 응답을 받는다."""
    slow = SlowI7(hold=1.0)     # 첫 요청이 스레드를 붙잡는 시간. 두 요청 어느 순서로 끝나도 판정(둘 다 200)은 같다
    try:
        from boundary_contracts.clients import HttpFailureStore
        api = Api(svc, {"t": Actor("dr-kim", "question-author")}, signals=HttpFailureStore(slow.url, "x", timeout=5))
        port, stop = start_server(api, request_timeout=5, max_concurrent=1)
        try:
            codes = []
            th = threading.Thread(target=lambda: codes.append(get(port, "/v1/signals")))
            th.start(); time.sleep(0.3)
            assert get(port, "/v1/drafts") == 200
            th.join(); assert codes == [200]
        finally:
            stop()
    finally:
        slow.close()


def _free_port():
    sk = socket.socket(); sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]; sk.close()
    return port


def _wait_healthy(port, limit=60.0):
    """서버가 요청을 받을 수 있을 때까지 기다린다(바깥 마감일 뿐 판정 값이 아니다. 부하에서 기동이 느릴 수 있다)."""
    end = time.monotonic() + limit
    while True:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2)
            return
        except OSError:
            assert time.monotonic() < end, "서버가 요청을 받지 못한다"
            time.sleep(0.05)


def _start(tmp_path, stub_url, grace):
    port = _free_port()
    env = dict(os.environ, QA_DEV_MODE="1", QA_DB_PATH=str(tmp_path / "q.db"), QA_PORT=str(port), QA_HOST="127.0.0.1",
               QA_AUTHOR_TOKENS="t:dr-kim:question-author", QA_SHUTDOWN_GRACE_SECONDS=str(grace),
               QA_REQUEST_TIMEOUT_SECONDS="30", QA_FAILURES_URL=stub_url, QA_FAILURES_TOKEN="x", QA_FAILURES_TIMEOUT_SECONDS="20",
               QA_SIGNALS_DEADLINE_SECONDS="30")
    p = subprocess.Popen([sys.executable, "-m", "question_authoring"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert "시작" in p.stdout.readline()
    _wait_healthy(port)
    return p, port


def _wait_line(p, needle, limit=60.0):
    """서버 표준 출력에서 needle이 든 줄을 기다린다(신호를 받아 처리했다는 사실을 시간이 아닌 출력으로 확인한다)."""
    found = []

    def reader():
        for line in p.stdout:
            if needle in line:
                found.append(line); return
    th = threading.Thread(target=reader, daemon=True); th.start(); th.join(limit)
    assert found, f"서버 출력에 {needle!r}이 없다"


def _signals_request(port):
    c = socket.create_connection(("127.0.0.1", port), timeout=20)
    c.sendall(b"GET /v1/signals HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer t\r\nConnection: close\r\n\r\n")
    return c


def test_sigterm_finishes_in_flight_request(tmp_path):
    slow = SlowI7()
    p, port = _start(tmp_path, slow.url, grace=30)
    try:
        c = _signals_request(port)
        assert slow.entered.wait(60)                       # 앱이 요청을 처리 중이다(대역이 요청을 받았다)
        p.send_signal(signal.SIGTERM)
        _wait_line(p, "종료 신호를 받았다")                  # 서버가 신호를 처리했다
        assert p.poll() is None                            # 요청이 끝나지 않았으므로 서버는 기다리고 있다
        slow.release.set()                                 # 이제 요청이 끝난다
        out = b""
        while True:
            chunk = c.recv(65536)
            if not chunk:
                break
            out += chunk
        assert b" 200 " in out.split(b"\r\n")[0]           # 진행 중이던 요청이 정상 응답을 받았다
        assert p.wait(timeout=60) == 0                     # 바깥 마감(매달리지 않음)
    finally:
        if p.poll() is None:
            p.kill()
        slow.close()


def test_sigterm_gives_up_after_grace(tmp_path):
    slow = SlowI7(hold=120.0)                              # 유예(1초)가 지나도 끝나지 않는 요청
    p, port = _start(tmp_path, slow.url, grace=1)
    try:
        c = _signals_request(port)
        assert slow.entered.wait(60)
        t = time.monotonic()
        p.send_signal(signal.SIGTERM)
        assert p.wait(timeout=60) == 0
        took = time.monotonic() - t
        # 아래 한계는 시간 자체를 재는 것이 아니다. 아래쪽(>=1초)은 유예를 기다렸다는 뜻이라 부하가 깨지 못한다.
        # 위쪽(<100초)은 '끝나지 않는 요청(120초)을 기다리지 않고 포기했다'는 뜻이다: 부하로 몇 초 늦어도 지나가고, 기다렸다면 120초에 걸린다
        assert 1.0 <= took < 100, took
        c.close()
    finally:
        if p.poll() is None:
            p.kill()
        slow.close()
