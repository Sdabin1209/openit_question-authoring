"""비밀은 NAME_FILE로 받고(D84), 값은 로그·/readyz·오류 본문·예외 메시지에 나오지 않는다."""
import json
import threading
import urllib.error
import urllib.request

import pytest
from boundary_contracts import SecretError

from question_authoring.__main__ import build
from question_authoring.http_api import make_server

AUTH_SECRET = "SECRET-author-tok-9f31"
READ_SECRET = "SECRET-read-tok-77ab"
FAIL_SECRET = "SECRET-fail-tok-c0de"
SECRETS = (AUTH_SECRET, READ_SECRET, FAIL_SECRET)


def _files(tmp_path, monkeypatch, author=None, read=None):
    for k in ("QA_AUTHOR_TOKENS", "QA_READ_TOKENS", "QA_FAILURES_TOKEN", "QA_FAILURES_URL"):
        monkeypatch.delenv(k, raising=False)
    a = tmp_path / "author"; a.write_text((author or f"{AUTH_SECRET}:dr-kim:question-author") + "\n")
    r = tmp_path / "read"; r.write_text((read or f"synthetic-data={READ_SECRET}") + "\n")
    monkeypatch.setenv("QA_AUTHOR_TOKENS_FILE", str(a))
    monkeypatch.setenv("QA_READ_TOKENS_FILE", str(r))
    monkeypatch.setenv("QA_DB_PATH", str(tmp_path / "q.sqlite3"))


def _call(base, path, token=None, method="GET"):
    req = urllib.request.Request(base + path, method=method, data=b"{}" if method == "POST" else None,
                                 headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_secrets_from_files_work_and_do_not_leak(tmp_path, monkeypatch, capsys):
    _files(tmp_path, monkeypatch)
    ff = tmp_path / "fail"; ff.write_text(FAIL_SECRET)
    monkeypatch.setenv("QA_FAILURES_TOKEN_FILE", str(ff))
    monkeypatch.setenv("QA_FAILURES_URL", "http://127.0.0.1:1")
    api, store = build()
    srv = make_server(api, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        bodies = []
        st, b = _call(base, "/v1/drafts", AUTH_SECRET); assert st == 200; bodies.append(b)
        st, b = _call(base, "/v1/question-sets", READ_SECRET); assert st == 200; bodies.append(b)
        st, b = _call(base, "/v1/drafts", READ_SECRET); assert st == 403; bodies.append(b)
        st, b = _call(base, "/v1/drafts", "wrong"); assert st == 403; bodies.append(b)
        st, b = _call(base, "/v1/drafts"); assert st == 403; bodies.append(b)
        st, b = _call(base, "/v1/signals", AUTH_SECRET); bodies.append(b)   # I7 불통: 오류 본문에도 토큰이 없어야 한다
        st, b = _call(base, "/readyz"); assert st == 200; bodies.append(b)
        st, b = _call(base, "/healthz"); bodies.append(b)
        st, b = _call(base, "/v1/nope", AUTH_SECRET, "POST"); bodies.append(b)
    finally:
        srv.shutdown(); srv.server_close()
    out = capsys.readouterr()
    text = "\n".join(bodies) + out.out + out.err
    for s in SECRETS:
        assert s not in text


def test_env_fallback_still_works_for_dev(tmp_path, monkeypatch):
    for k in ("QA_AUTHOR_TOKENS_FILE", "QA_READ_TOKENS_FILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("QA_AUTHOR_TOKENS", f"{AUTH_SECRET}:dr-kim:question-author")
    monkeypatch.setenv("QA_DB_PATH", str(tmp_path / "q.sqlite3"))
    api, _ = build()
    status, _ = api.handle("GET", "/v1/drafts", {"Authorization": f"Bearer {AUTH_SECRET}"}, b"")[:2]
    assert status == 200


def test_missing_or_empty_file_error_names_path_not_value(tmp_path, monkeypatch):
    _files(tmp_path, monkeypatch)
    monkeypatch.setenv("QA_AUTHOR_TOKENS_FILE", str(tmp_path / "absent"))
    with pytest.raises(SecretError) as e:
        build()
    assert not any(s in str(e.value) for s in SECRETS)
    (tmp_path / "empty").write_text("\n")
    monkeypatch.setenv("QA_AUTHOR_TOKENS_FILE", str(tmp_path / "empty"))
    with pytest.raises(SecretError):
        build()


def test_malformed_secret_exception_does_not_contain_value(tmp_path, monkeypatch):
    _files(tmp_path, monkeypatch, author=f"{AUTH_SECRET}-no-colons")
    with pytest.raises(Exception) as e:
        build()
    assert AUTH_SECRET not in str(e.value) and AUTH_SECRET not in repr(e.value)
    _files(tmp_path, monkeypatch, read=f"{READ_SECRET}-no-equals")
    with pytest.raises(Exception) as e:
        build()
    assert READ_SECRET not in str(e.value) and READ_SECRET not in repr(e.value)
