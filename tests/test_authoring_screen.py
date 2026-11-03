"""저작 화면(구조 점검 S21): 정적 파일이 열리고, 화면이 쓰는 경로는 모두 저작 API 계약 안에 있으며, 서버 값을 마크업으로 넣지 않는다."""
import io
import json
import re
from pathlib import Path

import pytest

from question_authoring.components.link_authoring_server import Api, _UI_FILES
from question_authoring.wiring.service import Actor

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "question_authoring" / "ui"


def get(api, path, headers=None):
    env = {"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": "", "CONTENT_LENGTH": "0", "wsgi.input": io.BytesIO(b"")}
    for k, v in (headers or {}).items():
        env["HTTP_" + k.upper().replace("-", "_")] = v
    return api.app.handle(env)


@pytest.fixture
def api(svc):
    return Api(svc, {"t": Actor("dr-kim", "question-author")}, read_tokens={"rd": "synthetic-data"}, gateway_tokens={"gw"})


@pytest.mark.parametrize("path,ctype", [("/ui/", "text/html"), ("/ui/index.html", "text/html"), ("/ui/app.js", "text/javascript"), ("/ui/app.css", "text/css")])
def test_static_files_are_served_with_type_and_csp(api, path, ctype):
    st, hdrs, body = get(api, path)
    assert st == 200 and hdrs["Content-Type"].startswith(ctype) and len(body) > 100
    assert "default-src 'self'" in hdrs["Content-Security-Policy"]


@pytest.mark.parametrize("path", ["/ui/../__main__.py", "/ui/secret.txt", "/ui/%2e%2e/x", "/ui/app.js/x"])
def test_only_listed_files_exist(api, path):
    assert get(api, path)[0] == 404


def test_every_api_path_the_screen_calls_is_a_route_of_the_contract():
    js = (UI / "app.js").read_text(encoding="utf-8")
    called = set(re.findall(r'"(/v1/[a-z/]+)', js)) | set(re.findall(r"`(/v1/[a-z/${}.()\w]+)", js))
    known = {"/v1/drafts", "/v1/materials", "/v1/reviews", "/v1/signals"}
    bases = {re.sub(r"/\$\{.*", "", c).rstrip("/") for c in called}
    assert bases <= known | {"/v1/drafts"}, bases


def test_screen_never_puts_server_values_into_markup():
    js = (UI / "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "outerHTML" not in js and "insertAdjacentHTML" not in js and "document.write" not in js and "eval(" not in js


def test_login_paths_are_the_contract_values_and_no_host_in_code():
    from boundary_contracts import load_schema
    js = (UI / "app.js").read_text(encoding="utf-8")
    h = load_schema("HumanAuthEndpoints")["properties"]
    for k in ("login", "logout", "userinfo", "idp_logout", "client_id"):
        assert h[k]["const"] in js, k
    assert not re.search(r"https?://", js + (UI / "index.html").read_text(encoding="utf-8"))


def test_screen_files_are_in_the_package_data():
    assert "ui/*" in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert set(_UI_FILES) == {p.name for p in UI.iterdir()}


def test_screen_script_is_valid_javascript():
    import shutil, subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음")
    r = subprocess.run([node, "--check", str(UI / "app.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
