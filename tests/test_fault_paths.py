"""장애 경로 시험(FAULT_MATRIX E2 저자 클라이언트 -> 저작 API, D5 개선 신호 읽기, 큰 자료, 원자성, 멱등).
PostgreSQL 항목은 TEST_DATABASE_URL이 없으면 SKIP(store 픽스처)."""
import contextlib
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from boundary_contracts.clients import HttpFailureStore
from boundary_contracts.errors import BoundaryError

from conftest import example_text
from helpers import publish
from question_authoring.http_api import Api, make_server, MAX_BODY
from question_authoring.service import Actor

AUTH = {"Authorization": "Bearer tok-a"}


@contextlib.contextmanager
def serving(api, **kw):
    srv = make_server(api, "127.0.0.1", 0, **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown(); srv.server_close()


def api_of(svc, **kw):
    return Api(svc, {"tok-a": Actor("dr-kim", "question-author")}, **kw)


def raw(port, data: bytes, *, wait=3.0, close_after_send=False) -> bytes:
    s = socket.create_connection(("127.0.0.1", port), timeout=wait)
    s.sendall(data)
    if close_after_send:
        s.close()
        return b""
    out = b""
    try:
        while True:
            c = s.recv(65536)
            if not c:
                break
            out += c
    except socket.timeout:
        out += b"<TIMEOUT>"
    s.close()
    return out


def post_head(path, n, extra=""):
    return (f"POST {path} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer tok-a\r\nContent-Length: {n}\r\n{extra}\r\n").encode()


MAT = "/v1/materials?question_set_id=qs-main&filename=f.txt"


# ---- E2 F2/F3: 멈춘 업로드 ----
def test_stalled_upload_is_cut_and_nothing_is_saved(svc):
    with serving(api_of(svc), request_timeout=1) as port:
        t = time.monotonic()
        out = raw(port, post_head(MAT, 5000) + b"x" * 10, wait=6)     # 본문이 모자란 채 멈춘다
        took = time.monotonic() - t
    assert b"<TIMEOUT>" not in out and took < 4, (out[:80], took)    # 서버가 먼저 끊었다
    assert svc.list_drafts() == []                                     # 반쯤 온 자료는 처리하지 않는다


# ---- E2 F1/F8 + 업로드 도중 연결 끊김 ----
def test_client_disconnect_mid_upload_saves_nothing(svc):
    data = example_text()
    with serving(api_of(svc), request_timeout=2) as port:
        raw(port, post_head(MAT, len(data)) + data[: len(data) // 2], close_after_send=True)
        time.sleep(0.5)
        assert svc.list_drafts() == []
        # 이어서 온전한 업로드는 정상 처리된다(서버가 막히지 않았다)
        req = urllib.request.Request(f"http://127.0.0.1:{port}{MAT}", data=data, headers=AUTH, method="POST")
        assert json.load(urllib.request.urlopen(req, timeout=10))["status"] == "PARSED"
    assert len(svc.list_drafts()) == 1


# ---- E2 F6: 깨진 요청 ----
@pytest.mark.parametrize("cl", ["abc", "-5"])
def test_bad_content_length_is_400(svc, cl):
    with serving(api_of(svc), request_timeout=2) as port:
        out = raw(port, (f"POST /v1/drafts HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer tok-a\r\nContent-Length: {cl}\r\n\r\n").encode())
    assert out.startswith(b"HTTP/1.0 400") or out.startswith(b"HTTP/1.1 400"), out[:60]


def test_oversize_and_non_json_are_400(svc):
    api = api_of(svc)
    assert api.handle("POST", "/v1/drafts", AUTH, b"not json")[0] == 400
    with serving(api, request_timeout=2) as port:
        out = raw(port, post_head(MAT, MAX_BODY + 1))
    assert b" 400 " in out.split(b"\r\n")[0]


# ---- E2 F5: 예상 밖의 서버 오류도 JSON으로 답한다(연결을 끊지 않는다) ----
def test_unexpected_error_is_json_503_without_details(svc, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("SECRET-internal-detail")
    monkeypatch.setattr(svc, "list_drafts", boom)
    api = api_of(svc)
    st, body = api.handle("GET", "/v1/drafts", AUTH, b"")
    assert st == 503 and body["code"] == "UNAVAILABLE" and "SECRET-internal-detail" not in json.dumps(body)
    with serving(api) as port:
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/v1/drafts", headers=AUTH), timeout=5)
        except urllib.error.HTTPError as e:
            assert e.code == 503 and json.load(e)["code"] == "UNAVAILABLE"


# ---- E2 F7 ----
def test_auth_failures_are_not_retryable(svc):
    api = api_of(svc)
    for h in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic x"}):
        st, body = api.handle("GET", "/v1/drafts", h, b"")
        assert st == 403 and body["retryable"] is False


# ---- E2 F4: 재시작해도 확정 데이터와 진행 중 초안이 남고, 재전송은 멱등 ----
def test_restart_keeps_drafts_and_confirmed_versions_and_resend_is_idempotent(tmp_path, validators):
    from question_authoring.service import AuthoringService
    from question_authoring.store import SqliteQuestionStore
    path = tmp_path / "q.sqlite3"

    def boot():
        s = SqliteQuestionStore(path)
        return s, api_of(AuthoringService(s, validators))
    s1, api1 = boot()
    st, out = api1.handle("POST", MAT, AUTH, example_text())
    did = out["draft_id"]
    s1.close()                                                   # 서버 재시작
    s2, api2 = boot()
    st, pv = api2.handle("GET", f"/v1/drafts/{did}/preview", AUTH, b"")
    assert st == 200 and pv["ready_to_confirm"]
    body = json.dumps({"preview_hash": pv["preview_hash"]}).encode()
    st, ref = api2.handle("POST", f"/v1/drafts/{did}/confirm", AUTH, body)
    assert st == 200
    s2.close()
    s3, api3 = boot()                                            # 확정 뒤 다시 재시작: 응답을 못 받은 저자가 같은 요청을 다시 보낸다
    st, ref2 = api3.handle("POST", f"/v1/drafts/{did}/confirm", AUTH, body)
    assert st == 200 and ref2 == ref
    assert s3.reader().list_versions("qs-main")[0] and len(s3.reader().list_versions("qs-main")[0]) == 1   # 버전은 하나
    st, err = api3.handle("POST", f"/v1/drafts/{did}/confirm", AUTH, json.dumps({"preview_hash": "sha256:" + "0" * 64}).encode())
    assert st == 409 and err["code"] == "CONFLICT"               # 다른 내용이면 CONFLICT
    s3.close()


# ---- 멱등: 같은 ID 같은 내용은 OK, 다른 내용은 CONFLICT (모든 저장소 구현) ----
def test_resend_same_ok_different_conflict(svc, store):
    ref = publish(svc)
    bank = store.get_question_bank(ref)
    rv = store.get_reviews(ref)[0]
    store.commit_version(bank, ref, rv, None, None)                         # 같은 버전·같은 내용: 조용히 성공
    other = json.loads(json.dumps(bank)); other["questions"][0]["name"] = "다른 이름"
    from question_authoring.store.base import bank_hash
    ref_o = dict(ref, content_hash=bank_hash(other))
    with pytest.raises(BoundaryError) as e:
        store.commit_version(other, ref_o, dict(rv, review_id="rv-other"), None, None)
    assert e.value.code == "CONFLICT"
    store.add_review(rv)                                                    # 같은 review_id·같은 내용
    with pytest.raises(BoundaryError) as e:
        store.add_review(dict(rv, reason="바뀐 내용"))
    assert e.value.code == "CONFLICT"
    assert len(store.get_reviews(ref)) == 1 and len(store.list_versions(ref["id"])[0]) == 1


# ---- D5: 개선 신호(I7) 읽기 ----
@contextlib.contextmanager
def i7_stub(mode: str):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            if mode == "slow":
                time.sleep(3)
            if mode == "5xx":
                self.send_response(500); self.send_header("Content-Length", "0"); self.end_headers(); return
            if mode == "garbage":
                d = b"<html>not json"
            elif mode == "shape":
                d = json.dumps({"items": "not-a-list"}).encode()
            elif mode == "pages":                               # 끝없이 이어지는 느린 목록
                time.sleep(0.4)
                d = json.dumps({"items": [], "next_cursor": "more"}).encode()
            else:
                d = b"{}"
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(d))); self.end_headers()
            try: self.wfile.write(d)
            except OSError: pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H); srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown(); srv.server_close()


@pytest.mark.parametrize("mode", ["5xx", "garbage", "shape"])
def test_d5_upstream_5xx_and_broken_response_are_503_not_success(svc, mode):
    with i7_stub(mode) as url:
        api = api_of(svc, signals=HttpFailureStore(url, "t", timeout=2))
        st, body = api.handle("GET", "/v1/signals", AUTH, b"")
    assert st == 503 and body["code"] == "UNAVAILABLE" and body["retryable"] is True
    assert api.handle("GET", "/v1/drafts", AUTH, b"")[0] == 200       # 저작은 영향 없다


def test_d5_slow_upstream_is_bounded_by_call_timeout(svc):
    with i7_stub("slow") as url:
        api = api_of(svc, signals=HttpFailureStore(url, "t", timeout=1))
        t = time.monotonic()
        st, body = api.handle("GET", "/v1/signals", AUTH, b"")
        took = time.monotonic() - t
    assert st == 503 and took < 2.5, took


def test_d5_total_deadline_bounds_many_slow_pages(svc):
    with i7_stub("pages") as url:
        api = api_of(svc, signals=HttpFailureStore(url, "t", timeout=2), signals_deadline=1.0)
        t = time.monotonic()
        st, body = api.handle("GET", "/v1/signals?max_cases=20000", AUTH, b"")
        took = time.monotonic() - t
    assert st == 503 and took < 3, took


def test_d5_trace_upstream_failure_is_not_counted_as_missing_trace(svc):
    """상류가 죽은 것을 trace 없음(TRACE_UNREADABLE)으로 조용히 세지 않는다."""
    from question_authoring.signals import build_signals

    class Src:
        def list_cases(self, **k):
            return [{"question_set": {"id": "qs-main"}, "deidentified_trace_ref": "t1", "failure_type": "X"}], None

        def get_trace(self, ref):
            raise BoundaryError("UNAVAILABLE", "상류 중단")
    with pytest.raises(BoundaryError) as e:
        build_signals(Src())
    assert e.value.code == "UNAVAILABLE"


# ---- 큰 자료의 처리 시간: 긴 한 줄이 제곱으로 느려지지 않는다(이메일 패턴이 그랬다) ----
def test_phi_check_is_linear_on_long_lines_and_still_detects(svc):
    from question_authoring.service import AuthoringService
    t = time.monotonic()
    assert AuthoringService._phi_warnings(b"x" * (2 * 1024 * 1024)) == []
    assert time.monotonic() - t < 2.0
    assert AuthoringService._phi_warnings("문의 kim.doctor+a@hospital.co.kr 입니다".encode()) == ["이메일 형태"]
    assert AuthoringService._phi_warnings(b"010-1234-5678") == ["전화번호 형태"]


def test_error_status_codes_match_contract_table(svc, monkeypatch):
    """QA-14: 상태 코드는 계약 CONVENTIONS.md 9절 표(독립 출처)에서 읽은 값과 같아야 한다. 표에 없는 코드(500, 413)를 내지 않는다."""
    import pathlib, re
    from question_authoring.http_api import STATUS
    doc = pathlib.Path(__file__).resolve().parents[2] / "boundary-contracts" / "CONVENTIONS.md"
    if not doc.exists():
        pytest.skip("계약 문서가 이웃 폴더에 없다")
    table = {}
    for line in doc.read_text(encoding="utf-8").split("## 9.")[1].split("HTTP 층의")[0].splitlines():
        m = re.match(r"\|\s*([A-Z_, ]+?)\s*\|\s*(\d{3})\s*\|", line)
        if m:
            for code in m.group(1).split(","):
                table[code.strip()] = int(m.group(2))
    assert table and STATUS == table, (STATUS, table)
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(svc, "list_drafts", boom)
    st, body = api_of(svc).handle("GET", "/v1/drafts", AUTH, b"")
    assert st == table[body["code"]]
