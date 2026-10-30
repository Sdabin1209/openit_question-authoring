"""토큰 의미 시험(표준성 점검 #12의 후속). 인증·401/403 규약은 openit_link.App이 맡는다(D118)이므로 여기서는 이 영역이 토큰에 붙이는 의미만 본다:
정적 토큰은 X-Openit-User를 무시하고, gateway 토큰은 그 사용자와 역할표를 쓰며, 읽기 토큰은 저작 경로에서 403이다.
상수 시간 비교(hmac.compare_digest)는 라이브러리 몫이다: 현재 v0.1.0은 dict 조회라 이 시험은 그것을 확인하지 않는다(docs/change-requests/CR-0008)."""
from question_authoring.components.link_authoring_server import Api
from question_authoring.wiring.service import Actor


def api_of(svc):
    return Api(svc, {"tok-a": Actor("dr-kim", "question-author")}, read_tokens={"rd": "synthetic-data"}, gateway_tokens={"g1"},
               user_roles={"u1": "question-author"})


def status(api, headers, path="/v1/drafts"):
    return api.handle("GET", path, headers)[0]


def test_static_token_ignores_user_header(svc):
    a = api_of(svc)
    assert status(a, {"Authorization": "Bearer tok-a", "X-Openit-User": "stranger"}) == 200


def test_gateway_requires_known_user(svc):
    a = api_of(svc)
    assert status(a, {"Authorization": "Bearer g1", "X-Openit-User": "u1"}) == 200
    assert status(a, {"Authorization": "Bearer g1"}) == 401
    assert status(a, {"Authorization": "Bearer g1", "X-Openit-User": "stranger"}) == 403


def test_read_token_reads_but_cannot_author(svc):
    a = api_of(svc)
    assert status(a, {"Authorization": "Bearer rd"}, "/v1/question-sets") == 200
    assert status(a, {"Authorization": "Bearer rd"}) == 403


def test_prefix_suffix_and_non_ascii_tokens_are_rejected(svc):
    a = api_of(svc)
    for t in ("tok-", "tok-aa", "토큰", ""):
        assert status(a, {"Authorization": "Bearer " + t}) == 401
    assert status(a, {}) == 401
