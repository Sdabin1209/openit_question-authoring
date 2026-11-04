"""RUNTIME R1.8·CONVENTIONS 13절: 이 영역이 만드는 모든 계약 클라이언트(I7 ops-feedback, I4 release-gate 판정 조회)는 OPENIT_EGRESS_PROXY 의 출구로만 나간다.
실제 서버 기동 경로(build)에서 만들어진 클라이언트가 CONNECT 줄을 자기 게이트웨이 대역에 보내는지 본다."""
import socket
import threading

import pytest

from question_authoring import __main__ as qa_main


class Connect:
    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.lines = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            with c:
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = c.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                self.lines.append(data.split(b"\r\n", 1)[0].decode())
                c.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")


@pytest.fixture
def build_env(monkeypatch, tmp_path):
    proxy = Connect()
    monkeypatch.setenv("QA_DB_PATH", str(tmp_path / "q.db"))
    monkeypatch.setenv("QA_DEV_MODE", "1")
    monkeypatch.setenv("QA_FAILURES_URL", "https://ops-feedback:8443")
    monkeypatch.setenv("QA_FAILURES_TOKEN", "t")
    monkeypatch.setenv("QA_GATE_URL", "https://release-gate:8443")
    monkeypatch.setenv("QA_GATE_TOKEN", "t")
    monkeypatch.setenv("OPENIT_EGRESS_PROXY", f"http://127.0.0.1:{proxy.port}")
    return proxy


def test_failure_and_gate_clients_connect_through_the_own_gateway_only(build_env):
    from boundary_contracts.errors import BoundaryError
    api, store = qa_main.build()
    try:
        with pytest.raises(BoundaryError) as e1:
            api.signals.list_cases(limit=1)
        with pytest.raises(BoundaryError) as e2:
            api.gate.find_decisions(question_set={"id": "qs-x", "version": None}, limit=1)
    finally:
        store.close()
    assert sorted(build_env.lines) == ["CONNECT ops-feedback:8443 HTTP/1.1", "CONNECT release-gate:8443 HTTP/1.1"]
    assert e1.value.code == e2.value.code == "PERMISSION_DENIED" and e1.value.details["reason"] == "EGRESS_DENIED"


def test_plain_http_neighbour_outside_loopback_is_refused_at_startup(monkeypatch, tmp_path):
    monkeypatch.setenv("QA_DB_PATH", str(tmp_path / "q.db"))
    monkeypatch.setenv("QA_DEV_MODE", "1")
    monkeypatch.setenv("QA_GATE_URL", "http://release-gate:8080")
    with pytest.raises(SystemExit):
        qa_main.build()
