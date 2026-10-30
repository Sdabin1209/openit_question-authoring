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
    """ops-feedback 대역: 목록 요청에 delay초 뒤 빈 목록으로 답한다(저작 서버의 요청 하나를 delay초 붙잡는다)."""
    def __init__(self, delay):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                time.sleep(delay)
                d = json.dumps({"items": [], "next_cursor": None}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(d))); self.end_headers()
                try: self.wfile.write(d)
                except OSError: pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H); self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
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
    port, stop = start_server(api, request_timeout=5)
    try:
        stalled = socket.create_connection(("127.0.0.1", port), timeout=5)
        stalled.sendall(b"POST /v1/drafts HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer t\r\nContent-Length: 100\r\n\r\n{")
        time.sleep(0.3)
        t = time.monotonic()
        assert get(port, "/v1/drafts") == 200 and time.monotonic() - t < 2
        stalled.close()
    finally:
        stop()


def test_requests_over_the_limit_wait_instead_of_failing(svc):
    """처리 스레드가 1개일 때 두 번째 요청은 거절(이전: 503)되지 않고 첫 요청이 끝나기를 기다렸다가 정상 응답을 받는다."""
    slow = SlowI7(1.0)
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


def _start(tmp_path, stub_url, grace):
    sk = socket.socket(); sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]; sk.close()
    env = dict(os.environ, QA_DEV_MODE="1", QA_DB_PATH=str(tmp_path / "q.db"), QA_PORT=str(port), QA_HOST="127.0.0.1",
               QA_AUTHOR_TOKENS="t:dr-kim:question-author", QA_SHUTDOWN_GRACE_SECONDS=str(grace),
               QA_REQUEST_TIMEOUT_SECONDS="30", QA_FAILURES_URL=stub_url, QA_FAILURES_TOKEN="x", QA_FAILURES_TIMEOUT_SECONDS="20",
               QA_SIGNALS_DEADLINE_SECONDS="30")
    p = subprocess.Popen([sys.executable, "-m", "question_authoring"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert "시작" in p.stdout.readline()
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1); break
        except OSError:
            time.sleep(0.1)
    return p, port


def _signals_request(port):
    c = socket.create_connection(("127.0.0.1", port), timeout=20)
    c.sendall(b"GET /v1/signals HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer t\r\nConnection: close\r\n\r\n")
    return c


def test_sigterm_finishes_in_flight_request(tmp_path):
    slow = SlowI7(2.0)
    p, port = _start(tmp_path, slow.url, grace=8)
    try:
        c = _signals_request(port)
        time.sleep(0.6)                                    # 앱이 요청을 처리 중이다
        p.send_signal(signal.SIGTERM)
        time.sleep(0.3)
        assert p.poll() is None                            # 기다리고 있다
        out = b""
        while True:
            chunk = c.recv(65536)
            if not chunk:
                break
            out += chunk
        assert b" 200 " in out.split(b"\r\n")[0]           # 진행 중이던 요청이 정상 응답을 받았다
        assert p.wait(timeout=10) == 0
    finally:
        if p.poll() is None:
            p.kill()
        slow.close()


def test_sigterm_gives_up_after_grace(tmp_path):
    slow = SlowI7(6.0)
    p, port = _start(tmp_path, slow.url, grace=1)
    try:
        c = _signals_request(port)
        time.sleep(0.6)
        t = time.monotonic()
        p.send_signal(signal.SIGTERM)
        assert p.wait(timeout=10) == 0
        assert 0.8 < time.monotonic() - t < 4
        c.close()
    finally:
        if p.poll() is None:
            p.kill()
        slow.close()
