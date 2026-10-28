"""표준성 점검 결정표 2절(question-authoring) #12 재현 시험.

#12 토큰 비교: 알려진 모든 토큰을 `hmac.compare_digest`로 끝까지 비교한다(조기 종료 없음).
    표준 근거: Python `hmac.compare_digest` 문서(타이밍 공격 대응용 상수 시간 비교), OWASP 타이밍 공격 대응.
    기대값은 "토큰이 N개면 비교가 정확히 N번, 맞는 토큰이 첫째여도 N번"이다(dict 조회·조기 종료는 이를 어긴다).
"""
import hmac

from question_authoring import http_api
from question_authoring.http_api import Api
from question_authoring.service import Actor


def _spy(monkeypatch):
    calls = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)
    monkeypatch.setattr(http_api.hmac, "compare_digest", spy, raising=False)
    return calls


def test_static_token_compares_every_candidate(svc, monkeypatch):
    calls = _spy(monkeypatch)
    toks = {"t-first": Actor("a1", "question-author"), "t-second": Actor("a2", "question-author"),
            "t-third": Actor("a3", "question-author")}
    api = Api(svc, toks)
    calls.clear()
    assert api._principal({"Authorization": "Bearer t-first"}).id == "a1"   # 첫 후보가 맞아도
    assert len(calls) == 3                                                  # 세 후보를 모두 비교한다
    calls.clear()
    assert api._principal({"Authorization": "Bearer nope"}) is None
    assert len(calls) == 3


def test_gateway_and_read_tokens_compare_every_candidate(svc, monkeypatch):
    calls = _spy(monkeypatch)
    api = Api(svc, {}, read_tokens={"r1": "synthetic-data", "r2": "model-lab"},
              gateway_tokens=["g1", "g2", "g3"], user_roles={"u1": "question-author"})
    calls.clear()
    assert api._principal({"Authorization": "Bearer g1", "X-Openit-User": "u1"}).id == "u1"
    assert len(calls) == 3 + 2      # 정적 0 + gateway 3 + 읽기 2: 어느 표에서 맞았는지와 무관하게 모두 비교한다(401/403 분류 도입 뒤)
    calls.clear()
    assert api.has_credentials({"Authorization": "Bearer r1"}) is True
    # 정적 0 + gateway 3 + 읽기 2를 모두 끝까지 비교
    assert len(calls) == 3 + 2
    calls.clear()
    api._reader_auth({"Authorization": "Bearer r1"})
    assert len(calls) >= 2


def test_token_semantics_unchanged(svc):
    api = Api(svc, {"tok-a": Actor("dr-kim", "question-author")}, read_tokens={"rd": "synthetic-data"})
    assert api._principal({"Authorization": "Bearer tok-a"}).id == "dr-kim"
    assert api._principal({"Authorization": "Bearer tok-"}) is None
    assert api._principal({"Authorization": "Bearer tok-aa"}) is None
    assert api.has_credentials({"Authorization": "Bearer rd"}) is True
    assert api.has_credentials({"Authorization": "Bearer x"}) is False
    assert api.has_credentials({}) is False


def test_non_ascii_token_does_not_crash(svc):
    # compare_digest는 str이 ASCII가 아니면 TypeError를 낸다. 바이트로 비교해 500/503 대신 거절해야 한다
    api = Api(svc, {"tok-a": Actor("dr-kim", "question-author")})
    assert api._principal({"Authorization": "Bearer 토큰"}) is None
