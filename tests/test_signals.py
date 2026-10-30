"""개선 신호: 작은 ops-feedback(I7) 서버 대역을 띄워 HttpFailureStore로 읽는다."""
import json
import pathlib
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from boundary_contracts import PIPELINE_SCHEMA_VERSION
from boundary_contracts.clients import HttpFailureStore

from question_authoring.components.link_authoring_server import Api, make_server
from question_authoring.wiring.service import Actor
from question_authoring.components.signals_build import question_ids_from_trace
from helpers import build_signals

# failure_type은 FailureCase 스키마에서 열거가 아닌 자유 문자열(minLength 1)이다. 시험은 뜻 없는 이름만 쓴다.
HASH = "sha256:" + "0" * 64


def case(i, ftype, qs="qs-main", status="PENDING", trace=None, with_set=True):
    c = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "case_id": f"fc-{i}", "version": 1, "created_at": "2026-10-15T00:00:00Z",
         "source_release_id": "rel-1", "runtime_contract_version": 1, "deidentified": True,
         "deidentified_trace_ref": trace or f"tr-{i}", "failure_type": ftype, "detection_method": "RULE",
         "review_status": status}
    if with_set:
        c["question_set"] = {"id": qs, "version": "1", "content_hash": HASH}
    return c


def _example_trace():
    """계약 OpsFeedbackDeidentifiedTrace 예시(2.5.1부터 get_trace가 이 스키마로 응답을 검사한다)."""
    import conformance
    p = pathlib.Path(conformance.__file__).parent / "examples" / "OpsFeedbackDeidentifiedTrace.json"
    return json.loads(p.read_text(encoding="utf-8"))


def trace_for(*qids):
    # 현행 TurnTrace: 한 턴은 문항 하나를 묻는다(extract_input.question_id). 발화는 비식별 처리된 값이다.
    (q,) = qids
    t = _example_trace()
    t["extract_input"]["question_id"] = q
    return t


def trace_without_question():
    t = _example_trace()
    t["extract_input"] = None
    return t


CASES = [case(1, "TYPE_A", trace="tr-a"), case(2, "TYPE_A", trace="tr-a2", status="CONFIRMED"),
         case(3, "TYPE_B", trace="tr-b"), case(4, "TYPE_A", trace="tr-none"),
         case(5, "X", trace="tr-gone"), case(6, "X", with_set=False), case(7, "TYPE_A", qs="qs-other", trace="tr-a")]
TRACES = {"tr-a": trace_for("Q1"), "tr-a2": trace_for("Q1"), "tr-b": trace_for("Q2"), "tr-none": trace_without_question()}


@pytest.fixture
def i7():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            u = urlparse(self.path); q = parse_qs(u.query)
            if self.headers.get("Authorization") != "Bearer i7-tok":
                return self._send(403, {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "code": "PERMISSION_DENIED", "message": "x", "retryable": False})
            if u.path == "/v1/cases":
                lim = int(q.get("limit", ["100"])[0]); start = int(q.get("cursor", ["0"])[0])
                items = CASES[start:start + lim]
                nxt = str(start + lim) if start + lim < len(CASES) else None
                return self._send(200, {"items": items, "next_cursor": nxt})
            if u.path.startswith("/v1/deidentified-traces/"):
                t = TRACES.get(u.path.rsplit("/", 1)[1])
                if t is None:
                    return self._send(404, {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "code": "NOT_FOUND", "message": "x", "retryable": False})
                return self._send(200, t)
            self._send(404, {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "code": "NOT_FOUND", "message": "x", "retryable": False})
        def _send(self, st, body):
            d = json.dumps(body).encode()
            self.send_response(st); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(d))); self.end_headers(); self.wfile.write(d)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown(); srv.server_close()


def test_trace_question_ids():
    assert question_ids_from_trace(trace_for("Q1")) == {"Q1"}
    # v1 모양은 읽지 않는다
    assert question_ids_from_trace({"planner_output": {"directive": {"targets": [{"question_id": "Q9"}]}}}) == set()
    assert question_ids_from_trace({"extract_input": None}) == set()
    assert question_ids_from_trace("x") == set()


def test_build_signals(i7):
    src = HttpFailureStore(i7, "i7-tok")
    out = build_signals(src, max_cases=100, now=None)
    assert out["status"] == "OK" and out["cases_read"] == 7 and not out["truncated"]
    main = next(s for s in out["question_sets"] if s["question_set_id"] == "qs-main")
    assert (main["case_count"], main["confirmed_count"]) == (5, 1)
    assert main["failure_types"] == {"TYPE_A": 3, "X": 1, "TYPE_B": 1}
    q1 = next(q for q in main["questions"] if q["question_id"] == "Q1")
    assert (q1["case_count"], q1["confirmed_count"]) == (2, 1)
    assert q1["failure_types"] == {"TYPE_A": 2}
    assert q1["confusions"] == [{"failure_type": "TYPE_A", "count": 2}]      # 같은 문항에서 반복
    q2 = next(q for q in main["questions"] if q["question_id"] == "Q2")
    assert q2["case_count"] == 1 and q2["confusions"] == []
    assert out["skipped"] == {"NO_QUESTION_ID": 1, "NO_QUESTION_SET": 1, "TRACE_UNREADABLE": 1}
    assert "MASKED" not in json.dumps(out) and "utterance" not in json.dumps(out)     # 발화는 담지 않는다
    # 필터와 여러 쪽, 상한
    only = build_signals(src, question_set_id="qs-other")
    assert [s["question_set_id"] for s in only["question_sets"]] == ["qs-other"]
    small = build_signals(src, max_cases=3)
    assert small["cases_read"] == 3 and small["truncated"]


def call(api, path, tok="auth-tok"):
    return api.handle("GET", path, {"Authorization": f"Bearer {tok}"}, b"")


def test_api_enabled_disabled_and_auth(svc, i7):
    actors = {"auth-tok": Actor("dr-kim", "question-author"), "visitor": Actor("v", "visitor")}
    on = Api(svc, actors, read_tokens={"read-tok": "synthetic-data"}, signals=HttpFailureStore(i7, "i7-tok"))
    st, body = call(on, "/v1/signals?question_set_id=qs-main")
    assert st == 200 and body["status"] == "OK" and [s["question_set_id"] for s in body["question_sets"]] == ["qs-main"]
    assert call(on, "/v1/signals", "read-tok")[0] == 403          # 읽기 토큰은 불가
    assert call(on, "/v1/signals", "visitor")[0] == 403           # 저자 역할이 아님
    assert call(on, "/v1/signals", "none")[0] == 401
    assert call(on, "/v1/signals?max_cases=0")[0] == 400
    off = Api(svc, actors)
    st, body = call(off, "/v1/signals")
    assert st == 200 and body["status"] == "DISABLED" and "QA_FAILURES_URL" in body["reason"]


def test_i7_failure_maps_to_boundary_error(svc, i7):
    api = Api(svc, {"auth-tok": Actor("dr-kim", "question-author")}, signals=HttpFailureStore(i7, "wrong"))
    st, body = call(api, "/v1/signals")
    assert st == 403 and body["code"] == "PERMISSION_DENIED"
    dead = Api(svc, {"auth-tok": Actor("dr-kim", "question-author")}, signals=HttpFailureStore("http://127.0.0.1:9", "x", timeout=1))
    st, body = call(dead, "/v1/signals")
    assert st == 503 and body["retryable"] is True
