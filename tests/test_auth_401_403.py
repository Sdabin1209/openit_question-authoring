"""계약 CONVENTIONS.md 6절 '401과 403'(D106, RFC 9110 15.5.2·11.6.1, RFC 6750 3절) 재현 시험.
공식 클라이언트가 아니라 raw HTTP(http.client)로 호출해 상태 코드와 헤더를 직접 본다.
기대값(계약): 토큰이 없거나 틀리면 401 + `WWW-Authenticate: Bearer`, 토큰은 맞지만 권한이 없으면 403,
사람 행위자 호출은 X-Openit-User가 없으면 401, 본문 code는 어느 쪽이든 PERMISSION_DENIED, retryable=false,
/healthz /readyz는 인증 없음."""
from helpers import HANG_GUARD_SECONDS
import http.client
import json
import threading

import pytest

from helpers import stop_http
from question_authoring.components.link_authoring_server import Api, make_server
from question_authoring.wiring.service import Actor

READ, AUTH, REVIEWER, GW = "read-tok", "auth-tok", "rev-tok", "gw-tok"


@pytest.fixture
def base(svc):
    api = Api(svc, {AUTH: Actor("dr-kim", "question-author"), REVIEWER: Actor("dr-lee", "clinical-reviewer")},
              read_tokens={READ: "synthetic-data"}, gateway_tokens={GW},
              user_roles={"u-kim": "question-author", "u-rev": "clinical-reviewer"})
    srv = make_server(api, "127.0.0.1", 0)
    threading.Thread(target=srv.run, daemon=True).start()
    yield srv.effective_port
    stop_http(srv)


def raw(port, method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=HANG_GUARD_SECONDS)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    out = (r.status, r.getheader("WWW-Authenticate"), json.loads(data) if data else None)
    c.close()
    return out


READ_PATHS = ["/v1/question-sets", "/v1/question-sets/qs-main/latest", "/v1/question-sets/qs-main/versions",
              "/v1/question-sets/qs-main/versions/1", "/v1/question-sets/qs-main/versions/1/reviews",
              "/v1/question-sets/qs-main/versions/1/change"]
AUTHORING = [("GET", "/v1/drafts"), ("POST", "/v1/drafts"), ("GET", "/v1/drafts/x"), ("GET", "/v1/drafts/x/preview"),
             ("POST", "/v1/drafts/x/edits"), ("POST", "/v1/drafts/x/confirm"), ("POST", "/v1/reviews"),
             ("POST", "/v1/materials?question_set_id=qs&filename=f.txt"), ("GET", "/v1/signals")]


def check_401(res):
    st, www, body = res
    assert st == 401 and www == "Bearer"
    assert body["code"] == "PERMISSION_DENIED" and body["retryable"] is False


@pytest.mark.parametrize("path", READ_PATHS)
def test_read_surface_missing_or_wrong_token_is_401(base, path):
    check_401(raw(base, "GET", path))
    check_401(raw(base, "GET", path, {"Authorization": "Bearer wrong"}))
    check_401(raw(base, "GET", path, {"Authorization": "Basic abc"}))


@pytest.mark.parametrize("method,path", AUTHORING)
def test_authoring_missing_or_wrong_token_is_401(base, method, path):
    body = b"{}" if method == "POST" else None
    check_401(raw(base, method, path, None, body))
    check_401(raw(base, method, path, {"Authorization": "Bearer wrong"}, body))


@pytest.mark.parametrize("method,path", AUTHORING)
def test_read_token_on_authoring_is_403_without_challenge(base, method, path):
    st, www, body = raw(base, method, path, {"Authorization": f"Bearer {READ}"}, b"{}" if method == "POST" else None)
    assert st == 403 and www is None and body["code"] == "PERMISSION_DENIED" and body["retryable"] is False


def test_valid_token_wrong_role_is_403(base):
    st, www, body = raw(base, "POST", "/v1/drafts", {"Authorization": f"Bearer {REVIEWER}"},
                        json.dumps({"question_set_id": "qs-x"}).encode())
    assert st == 403 and www is None and body["code"] == "PERMISSION_DENIED"


def test_gateway_token_without_user_header_is_401(base):
    check_401(raw(base, "GET", "/v1/drafts", {"Authorization": f"Bearer {GW}"}))
    # 본문 검증(400)은 라이브러리가 처리 함수보다 먼저 하므로 모양이 맞는 본문을 보낸다(검증 순서 변화는 헤드가 받아들였다, CR-0008)
    ok = json.dumps({"question_set": {"id": "q", "version": "1", "content_hash": "sha256:" + "a" * 64}, "decision": "APPROVED", "clinical_basis_approved": False}).encode()
    check_401(raw(base, "POST", "/v1/reviews", {"Authorization": f"Bearer {GW}"}, ok))
    check_401(raw(base, "GET", "/v1/question-sets", {"Authorization": f"Bearer {GW}"}))


def test_gateway_unknown_or_unpermitted_user_is_403(base):
    st, www, body = raw(base, "GET", "/v1/drafts", {"Authorization": f"Bearer {GW}", "X-Openit-User": "stranger"})
    assert st == 403 and www is None and body["code"] == "PERMISSION_DENIED"
    st, www, _ = raw(base, "POST", "/v1/drafts", {"Authorization": f"Bearer {GW}", "X-Openit-User": "u-rev"}, b'{"question_set_id":"q"}')
    assert st == 403 and www is None


def test_valid_credentials_unchanged_and_health_open(base):
    assert raw(base, "GET", "/healthz")[0] == 200
    assert raw(base, "GET", "/readyz")[0] == 200
    assert raw(base, "GET", "/v1/question-sets", {"Authorization": f"Bearer {READ}"})[0] == 200
    assert raw(base, "GET", "/v1/drafts", {"Authorization": f"Bearer {AUTH}"})[0] == 200
    assert raw(base, "GET", "/v1/drafts", {"Authorization": f"Bearer {GW}", "X-Openit-User": "u-kim"})[0] == 200
