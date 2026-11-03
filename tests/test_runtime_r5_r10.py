"""RUNTIME R5.1(HealthStatus·not_ready)·R10.11(/metrics 인증 없음, DB 불가에도 200)·CONVENTIONS 6절(신원 헤더는 게이트웨이 토큰 요청에서만)."""
import json

import pytest
from boundary_contracts import validator

from question_authoring.components.link_authoring_server import Api
from question_authoring.wiring.service import Actor

KIM = Actor("dr-kim", "question-author")
REV = Actor("dr-lee", "clinical-reviewer")


def api_of(svc, ready=lambda: True):
    return Api(svc, {"tok-a": KIM, "tok-r": REV}, read_tokens={"rd": "synthetic-data"}, gateway_tokens={"gw"}, ready=ready, db_kind="sqlite")


def test_readyz_not_ready_uses_the_canonical_spelling_and_passes_both_schemas(svc):
    st, body = api_of(svc, ready=lambda: False).handle("GET", "/readyz", {})
    assert st == 503 and body["status"] == "not_ready"
    assert not list(validator("HealthStatus").iter_errors(body))                       # 공통 모양(R5.1)
    from question_authoring.wiring import contract
    assert contract.problems("QuestionAuthoringDeployInterface", "ReadyzResponse", body) == []   # 영역별 모양
    st, body = api_of(svc).handle("GET", "/readyz", {})
    assert st == 200 and body["status"] == "ready" and not list(validator("HealthStatus").iter_errors(body))


def test_healthz_body_passes_health_status(svc):
    st, body = api_of(svc).handle("GET", "/healthz", {})
    assert st == 200 and body and not list(validator("HealthStatus").iter_errors(body))


@pytest.mark.parametrize("auth", [None, "Bearer tok-a", "Bearer wrong", "Bearer rd", "Bearer gw"])
def test_metrics_needs_no_auth_and_ignores_tokens_even_when_the_store_is_down(svc, auth, monkeypatch):
    """R10.11: 토큰이 있어도 틀려도 200 Prometheus 텍스트, DB 불가(R3.10)에도 200(프로세스 안의 값만)."""
    a = api_of(svc, ready=lambda: False)
    monkeypatch.setattr(svc, "list_drafts", lambda *x, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    h = {} if auth is None else {"Authorization": auth}
    import io
    env = {"REQUEST_METHOD": "GET", "PATH_INFO": "/metrics", "QUERY_STRING": "", "CONTENT_LENGTH": "0", "wsgi.input": io.BytesIO(b"")}
    if auth:
        env["HTTP_AUTHORIZATION"] = auth
    st, hdrs, data = a.app.handle(env)
    text = data.decode()
    assert st == 200 and hdrs["Content-Type"].startswith("text/plain; version=0.0.4")
    assert "qa_up 1" in text and "# TYPE qa_http_requests_total counter" in text


def test_metrics_counts_requests_by_pattern_and_status(svc):
    a = api_of(svc)
    a.handle("GET", "/v1/drafts", {"Authorization": "Bearer tok-a"})
    a.handle("GET", "/v1/drafts", {"Authorization": "Bearer wrong"})
    t = a.metrics_text()
    assert 'qa_http_requests_total{pattern="/v1/drafts",status="200"} 1' in t
    assert 'qa_http_requests_total{pattern="/v1/drafts",status="401"} 1' in t


@pytest.mark.parametrize("tok", ["tok-a", "tok-r", "rd"])
def test_identity_headers_are_ignored_on_every_non_gateway_token(svc, tok):
    """CONVENTIONS 6절(3.4.5): X-Openit-User/Roles/Device 는 게이트웨이 토큰이 붙은 요청에서만 쓴다. 서비스에 있는 다른 토큰은 모두 무시."""
    a = api_of(svc)
    h = {"Authorization": f"Bearer {tok}", "X-Openit-User": "forged", "X-Openit-Roles": "author,clinical-reviewer,admin", "X-Openit-Device": "dev-1"}
    st, body = a.handle("POST", "/v1/drafts", h, json.dumps({"question_set_id": "qs-h"}).encode())
    if tok == "tok-a":
        assert st == 200 and body["created_by"] == "dr-kim"          # 토큰에 묶인 행위자이지 헤더가 아니다
    elif tok == "tok-r":
        assert st == 403                                              # 검수 토큰은 헤더로 저자가 되지 못한다
    else:
        assert st == 403                                              # 읽기 토큰은 헤더로 쓰기 권한을 얻지 못한다


def test_gateway_token_without_the_roles_header_is_403_and_with_it_works(svc):
    a = api_of(svc)
    b = json.dumps({"question_set_id": "qs-g"}).encode()
    assert a.handle("POST", "/v1/drafts", {"Authorization": "Bearer gw", "X-Openit-User": "u1"}, b)[0] == 403
    st, body = a.handle("POST", "/v1/drafts", {"Authorization": "Bearer gw", "X-Openit-User": "u1", "X-Openit-Roles": "author"}, b)
    assert st == 200 and body["created_by"] == "u1"
