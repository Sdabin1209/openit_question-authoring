"""토큰 목록 비밀의 형식은 계약(secret_format_problems, 5.0.1)이 정한다. 이 영역은 읽을 때 그 검사를 지나게 한다(D117). 값은 오류에 싣지 않는다."""
import pytest

from question_authoring import __main__ as m


def test_valid_token_lists_pass(monkeypatch):
    monkeypatch.setenv("QA_AUTHOR_TOKENS", "tok1:dr-kim:question-author,tok2:dr-lee:clinical-reviewer")
    monkeypatch.setenv("QA_GATEWAY_TOKENS", "gw1,gw2")
    monkeypatch.setenv("QA_READ_TOKENS", "synthetic-data=rt1")
    assert m._secret_list("QA_AUTHOR_TOKENS", "token_actor_role_list").count(",") == 1
    assert m._secret_list("QA_GATEWAY_TOKENS", "token_list") == "gw1,gw2"
    assert m._secret_list("QA_READ_TOKENS", "role_token_list") == "synthetic-data=rt1"


@pytest.mark.parametrize("name,kind,bad", [("QA_AUTHOR_TOKENS", "token_actor_role_list", "only-two:parts"),
                                           ("QA_GATEWAY_TOKENS", "token_list", "a,,b"),
                                           ("QA_READ_TOKENS", "role_token_list", "no-equals")])
def test_malformed_lists_stop_startup_without_printing_the_value(monkeypatch, name, kind, bad):
    monkeypatch.setenv(name, bad)
    with pytest.raises(SystemExit) as e:
        m._secret_list(name, kind)
    assert name in str(e.value) and bad not in str(e.value)


def test_unset_is_empty(monkeypatch):
    monkeypatch.delenv("QA_GATEWAY_TOKENS", raising=False)
    monkeypatch.delenv("QA_GATEWAY_TOKENS_FILE", raising=False)
    assert m._secret_list("QA_GATEWAY_TOKENS", "token_list") == ""
