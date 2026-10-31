"""HTTP 서버에 넘기는 설정과 종료 절차(단위 4a, D111; U421 정정).
waitress가 느린 연결을 N초에 끊는가, 실제 SIGTERM에 프로세스가 끝나는가는 라이브러리·운영체제의 동작이라 영역 시험이 아니다(deploy smoke/E2E의 몫).
이 영역의 책임은 (1) 서버에 올바른 값을 넘겼는가 (2) 우리 종료 절차(drain, close_all)가 맞게 동작하는가이다. 시간을 재지 않는다."""
import threading
import types

import pytest

from helpers import HANG_GUARD_SECONDS, start_server, stop_http
from question_authoring import __main__ as qa_main
from question_authoring.components.link_authoring_server import MAX_BODY, Api, drain, make_server
from question_authoring.components.link_authoring_server import close_all   # noqa: F401
from question_authoring.wiring.service import Actor


def _api(svc):
    return Api(svc, {"t": Actor("dr-kim", "question-author")})


# ---- (1) 서버에 넘기는 값 ----
def test_make_server_passes_the_limits_to_waitress(svc):
    srv = make_server(_api(svc), "127.0.0.1", 0, request_timeout=7, max_concurrent=3)
    try:
        a = srv.adj
        assert a.channel_timeout == 7                    # 멈춘 연결을 끊는 시간 = 요청 무응답 상한
        assert a.threads == 3                            # 동시 처리 요청 수 = max_concurrent (넘으면 줄 서서 기다린다)
        assert a.max_request_body_size == MAX_BODY       # 본문 상한 초과는 헤더 단계에서 413
        assert a.cleanup_interval == 1                   # channel_timeout이 늦게 적용되지 않게 하는 최솟값
        assert a.clear_untrusted_proxy_headers is True
    finally:
        srv.close()


def test_make_server_defaults(svc):
    srv = make_server(_api(svc), "127.0.0.1", 0)
    try:
        assert srv.adj.channel_timeout == 30 and srv.adj.threads == 32
    finally:
        srv.close()


def test_every_route_has_the_body_limit(svc):
    routes = [r for r, _ in _api(svc).app._routes]
    assert routes and all(r.max_body == MAX_BODY for r in routes)    # 라이브러리 Route.max_body도 같은 상한(초과는 413)


def test_entry_point_passes_env_limits_to_the_server(monkeypatch, tmp_path):
    seen = {}

    def fake_make_server(api, host, port, **kw):
        seen.update(host=host, port=port, **kw)
        raise SystemExit(0)                              # 서버를 띄우지 않고 넘긴 값만 본다
    monkeypatch.setattr(qa_main, "make_server", fake_make_server)
    monkeypatch.setenv("QA_DB_PATH", str(tmp_path / "q.db"))
    monkeypatch.setenv("QA_REQUEST_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("QA_MAX_CONCURRENT_REQUESTS", "3")
    monkeypatch.setenv("QA_HOST", "127.0.0.1")
    monkeypatch.setenv("QA_PORT", "8123")
    with pytest.raises(SystemExit):
        qa_main.main([])
    assert seen == {"host": "127.0.0.1", "port": 8123, "request_timeout": 7.0, "max_concurrent": 3}


def test_entry_point_passes_signal_timeouts_to_the_api(monkeypatch, tmp_path):
    monkeypatch.setenv("QA_DB_PATH", str(tmp_path / "q.db"))
    monkeypatch.setenv("QA_FAILURES_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("QA_FAILURES_TOKEN", "x")
    monkeypatch.setenv("QA_FAILURES_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("QA_SIGNALS_DEADLINE_SECONDS", "11")
    api, store = qa_main.build()
    try:
        assert api.signals._timeout == 7.0               # 호출 하나의 읽기 상한(I7 클라이언트에 넘긴 값)
        assert api.signals_deadline == 11.0              # GET /v1/signals 한 번의 총 마감
    finally:
        store.close()


# ---- (2) 종료 절차 ----
class FakeClock:
    """시험이 쥔 시계. 시간은 sleep을 부를 때만 흐른다(실제로 기다리지 않는다)."""
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, secs):
        self.now += secs


class _Dispatcher:
    def __init__(self, active, queue=0):
        self.active_count, self.queue = active, [0] * queue


def _srv(active, queue=0, outbuf=0):
    conn = types.SimpleNamespace(total_outbufs_len=outbuf)
    return types.SimpleNamespace(accepting=True, task_dispatcher=_Dispatcher(active, queue), _map={1: conn})


def test_drain_stops_accepting_and_reports_requests_unfinished_after_the_grace_period():
    clock, busy = FakeClock(), _srv(active=2, queue=1)
    assert drain(busy, 1.0, clock=clock.monotonic, sleep=clock.sleep) == 3    # 유예(시험 시계로 1초)가 지나도 못 끝낸 요청 수: 처리 중 2 + 대기 1
    assert busy.accepting is False                                              # 새 연결을 받지 않는다
    assert clock.now >= 1.0                                                     # 유예를 다 기다렸다(시험 시계 기준)


def test_drain_returns_immediately_when_nothing_is_in_flight():
    clock, idle = FakeClock(), _srv(active=0)
    assert drain(idle, 8.0, clock=clock.monotonic, sleep=clock.sleep) == 0 and idle.accepting is False
    assert clock.now == 0.0                                                     # 기다리지 않았다


def test_drain_returns_as_soon_as_in_flight_requests_finish():
    clock, srv = FakeClock(), _srv(active=1)
    polls = []

    class Finishing:
        queue = []

        @property
        def active_count(self):                                                 # 세 번 확인한 뒤 요청이 끝난다
            polls.append(1)
            return 1 if len(polls) < 3 else 0
    srv.task_dispatcher = Finishing()
    assert drain(srv, 8.0, clock=clock.monotonic, sleep=clock.sleep) == 0
    assert clock.now < 8.0                                                      # 유예를 다 쓰기 전에 돌아왔다(시험 시계 기준)


def test_drain_waits_for_pending_response_bytes_until_the_grace_period_ends():
    clock, srv = FakeClock(), _srv(active=0, outbuf=10)                         # 요청은 끝났지만 응답이 아직 안 나갔다
    assert drain(srv, 2.0, clock=clock.monotonic, sleep=clock.sleep) == 0
    assert clock.now >= 2.0


def test_close_all_ends_the_server_loop_without_any_signal(svc):
    """근본 원인 재현(U308)의 영역 몫: 종료가 SIGINT 전달에 기대지 않는다. 서버 루프 안에서 소켓을 모두 닫으면 run()이 스스로 돌아온다."""
    srv = make_server(_api(svc), "127.0.0.1", 0)
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    srv.trigger.pull_trigger(lambda: close_all(srv))
    th.join(HANG_GUARD_SECONDS)                                  # 매달림 감시: 정상은 수 ms
    assert not th.is_alive()
    srv.task_dispatcher.shutdown(cancel_pending=True, timeout=HANG_GUARD_SECONDS)    # 매달림 감시


def test_start_server_helper_serves_one_round_trip(svc):
    import urllib.request
    port, stop = start_server(_api(svc))
    try:
        assert urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=HANG_GUARD_SECONDS).status == 200   # 매달림 감시
    finally:
        stop()
