"""장애 경로 시험(FAULT_MATRIX E2 저자 클라이언트 -> 저작 API, D5 개선 신호 읽기, 큰 자료, 원자성, 멱등).
PostgreSQL 항목은 TEST_DATABASE_URL이 없으면 SKIP(store 픽스처)."""
from helpers import phi_warnings
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
from helpers import AUTHOR, HANG_GUARD_SECONDS, stop_http
from question_authoring.components.link_authoring_server import Api, make_server, MAX_BODY
from question_authoring.wiring.service import Actor

AUTH = {"Authorization": "Bearer tok-a"}


@contextlib.contextmanager
def serving(api, **kw):
    srv = make_server(api, "127.0.0.1", 0, **kw)
    threading.Thread(target=srv.run, daemon=True).start()
    try:
        yield srv.effective_port
    finally:
        stop_http(srv)


def api_of(svc, **kw):
    return Api(svc, {"tok-a": Actor("dr-kim", "question-author")}, **kw)


def raw(port, data: bytes, *, wait=HANG_GUARD_SECONDS, close_after_send=False) -> bytes:   # wait: 매달림 감시(소켓 읽기 마감)
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


# ---- E2 F2/F3, F1/F8: 멈춘 업로드·연결 끊김 ----
# waitress가 멈춘 업로드를 N초에 끊는가, 본문이 모자란 요청을 앱에 넘기지 않는가는 라이브러리 동작이라 시험하지 않는다(U421 정정).
# 영역의 몫은 넘긴 값이다: 멈춘 연결을 끊는 시간 = 요청 무응답 상한(tests/test_server_properties.py::test_make_server_passes_the_limits_to_waitress).
def test_upload_is_atomic_when_extraction_fails(svc):
    """처리 도중 실패한 업로드는 반쯤 처리된 초안을 남기지 않는다: 읽지 못하면 빈 초안으로 폴백한다(화면 입력으로 이어 가기)."""
    import types
    from question_authoring.wiring.service import AuthoringService

    def boom(inp):
        raise RuntimeError("추출 중 끊김")
    failing = types.SimpleNamespace(NAME="boom", VERSION="0", INPUT_SCHEMA="MaterialExtractRequest", OUTPUT_SCHEMA="MaterialExtraction",
                                    can_handle=lambda filename: True, run=boom, __name__="boom")
    s2 = AuthoringService(svc.store, extractors=(failing,), require_two_person=False)
    out = s2.ingest_material(AUTHOR, "qs-main", "f.txt", example_text())
    assert out["status"] == "FALLBACK"
    assert len(svc.list_drafts()) == 1 and svc.get_draft(out["draft_id"])["bank"] == {"questions": []}


# ---- E2 F6: 깨진 요청 ----
@pytest.mark.parametrize("cl", ["abc", "-5"])
def test_bad_content_length_is_400(svc, cl):
    with serving(api_of(svc), request_timeout=HANG_GUARD_SECONDS) as port:
        out = raw(port, (f"POST /v1/drafts HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer tok-a\r\nContent-Length: {cl}\r\n\r\n").encode())
    assert out.startswith(b"HTTP/1.0 400") or out.startswith(b"HTTP/1.1 400"), out[:60]


def test_non_json_is_400(svc):
    assert api_of(svc).handle("POST", "/v1/drafts", AUTH, b"not json")[0] == 400


def test_oversize_is_413_before_the_body_is_sent(svc):
    """동작 변경(단위 4a): 본문 상한 초과는 waitress가 헤더 단계에서 413(RFC 9110 15.5.14)으로 거절한다. 이전에는 앱이 400(계약 CONVENTIONS 9절 표)을 냈다. ADR 0016"""
    with serving(api_of(svc), request_timeout=HANG_GUARD_SECONDS) as port:
        out = raw(port, post_head(MAT, MAX_BODY + 1))      # 본문은 보내지 않는다
    assert b" 413 " in out.split(b"\r\n")[0], out[:80]


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
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/v1/drafts", headers=AUTH), timeout=HANG_GUARD_SECONDS)   # 매달림 감시
        except urllib.error.HTTPError as e:
            assert e.code == 503 and json.load(e)["code"] == "UNAVAILABLE"


# ---- E2 F7 ----
def test_auth_failures_are_not_retryable(svc):
    api = api_of(svc)
    for h in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic x"}):
        st, body = api.handle("GET", "/v1/drafts", h, b"")
        assert st == 401 and body["retryable"] is False   # 토큰 없음·틀림은 401(계약 CONVENTIONS 6절)


# ---- E2 F4: 재시작해도 확정 데이터와 진행 중 초안이 남고, 재전송은 멱등 ----
def test_restart_keeps_drafts_and_confirmed_versions_and_resend_is_idempotent(tmp_path, validators):
    from question_authoring.wiring.service import AuthoringService
    from question_authoring.wiring.stores import SqliteQuestionStore
    path = tmp_path / "q.sqlite3"

    def boot():
        s = SqliteQuestionStore(path)
        return s, api_of(AuthoringService(s))
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
    from helpers import bank_hash
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
    """ops-feedback 대역. 모든 모드가 즉시 답한다(시간에 기대지 않는다)."""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            if mode == "5xx":
                self.send_response(500); self.send_header("Content-Length", "0"); self.end_headers(); return
            if mode == "garbage":
                d = b"<html>not json"
            elif mode == "shape":
                d = json.dumps({"items": "not-a-list"}).encode()
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
        api = api_of(svc, signals=HttpFailureStore(url, "t", timeout=HANG_GUARD_SECONDS))    # timeout: 매달림 감시(이 시험의 주제가 아니다)
        st, body = api.handle("GET", "/v1/signals", AUTH, b"")
    assert st == 503 and body["code"] == "UNAVAILABLE" and body["retryable"] is True
    assert api.handle("GET", "/v1/drafts", AUTH, b"")[0] == 200       # 저작은 영향 없다


class _FailingSource:
    """즉시 UNAVAILABLE(시간 초과 포함)을 던지는 I7 대역. 실제 소켓·시계를 쓰지 않는다."""
    def __init__(self, exc):
        self.exc = exc

    def list_cases(self, **k):
        raise self.exc

    def get_trace(self, ref):
        raise self.exc


def test_d5_upstream_timeout_is_503_retryable_and_authoring_is_unaffected(svc):
    """호출 시간 초과는 라이브러리(urllib)가 낸다. 우리 몫은 그것이 UNAVAILABLE(재시도 가능)로 나가고 저작 경로가 영향받지 않는 것이다(U421 정정)."""
    api = api_of(svc, signals=_FailingSource(BoundaryError("UNAVAILABLE", "읽기 시간 초과")))
    st, body = api.handle("GET", "/v1/signals", AUTH, b"")
    assert st == 503 and body["code"] == "UNAVAILABLE" and body["retryable"] is True
    assert api.handle("GET", "/v1/drafts", AUTH, b"")[0] == 200


def test_d5_total_deadline_stops_endless_pages_with_an_injected_clock(svc):
    """총 마감(signals_deadline)은 우리 코드(link_failures_read)의 판단이다. 서비스에 시험이 쥔 시계(쪽 하나를 읽을 때마다 0.4 흐른다)를 주입해
    끝없이 이어지는 목록이 마감에서 UNAVAILABLE로 끊기는지 본다. 실제 시간은 흐르지 않고 재지 않는다."""
    from question_authoring.wiring.service import AuthoringService
    now = [0.0]

    class Pages:
        reads = 0

        def list_cases(self, **k):
            Pages.reads += 1
            now[0] += 0.4                                            # 쪽 하나를 읽는 데 0.4초가 걸렸다고 친다
            return [], "more"                                        # 끝없이 다음 쪽이 있다

        def get_trace(self, ref):
            raise AssertionError("사례가 없으니 trace를 읽지 않는다")
    timed = AuthoringService(svc.store, require_two_person=False, monotonic=lambda: now[0])
    api = api_of(timed, signals=Pages(), signals_deadline=1.0)
    st, body = api.handle("GET", "/v1/signals?max_cases=20000", AUTH, b"")
    assert st == 503 and body["code"] == "UNAVAILABLE"
    assert Pages.reads == 3                                          # 0.4, 0.8, 1.2: 세 번째 읽은 뒤 마감(1.0)을 넘어 멈췄다


def test_d5_deadline_value_reaches_the_reader(svc):
    """마감 값이 읽기 컴포넌트에 그대로 닿는다(실제 시간을 재지 않고 넘긴 값을 본다)."""
    seen = {}
    real = svc.build_signals

    def spy(source, **kw):
        seen.update(kw)
        return real(source, **kw)
    svc.build_signals = spy
    api = api_of(svc, signals=_FailingSource(BoundaryError("UNAVAILABLE", "x")), signals_deadline=7.5)
    api.handle("GET", "/v1/signals", AUTH, b"")
    assert seen["deadline_seconds"] == 7.5


def test_d5_trace_upstream_failure_is_not_counted_as_missing_trace(svc):
    """상류가 죽은 것을 trace 없음(TRACE_UNREADABLE)으로 조용히 세지 않는다."""
    from helpers import build_signals

    class Src:
        def list_cases(self, **k):
            return [{"question_set": {"id": "qs-main"}, "deidentified_trace_ref": "t1", "failure_type": "X"}], None

        def get_trace(self, ref):
            raise BoundaryError("UNAVAILABLE", "상류 중단")
    with pytest.raises(BoundaryError) as e:
        build_signals(Src())
    assert e.value.code == "UNAVAILABLE"


# ---- 큰 자료의 처리 시간: 긴 한 줄이 제곱으로 느려지지 않는다(이메일 패턴이 그랬다) ----
def test_phi_check_finishes_on_long_lines_and_still_detects(svc):
    """긴 한 줄에서 정규식이 제곱 시간으로 느려지지 않는다(이메일 패턴이 그랬다). 시간을 재지 않고 끝나는지만 본다:
    제곱이면 2MiB에서 수 시간이라 매달림 감시(HANG_GUARD_SECONDS)에 걸려 실패한다. 정상은 0.1초대."""
    from helpers import run_bounded
    assert run_bounded(lambda: phi_warnings(b"x" * (2 * 1024 * 1024))) == []
    assert phi_warnings("문의 kim.doctor+a@hospital.co.kr 입니다".encode()) == ["이메일 형태"]
    assert phi_warnings(b"010-1234-5678") == ["전화번호 형태"]


def test_error_status_codes_match_contract_table(svc, monkeypatch):
    """QA-14: 상태 코드는 계약 CONVENTIONS.md 9절 표(독립 출처)에서 읽은 값과 같아야 한다. 표에 없는 코드(500, 413)를 내지 않는다."""
    import pathlib, re
    from question_authoring.components.link_authoring_server import STATUS
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
