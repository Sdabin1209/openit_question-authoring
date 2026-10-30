"""노출 선언(계약 Exposure, 이미지 라벨 io.openit.exposure, U47): 정본 파일 deploy-exposure.json이 계약을 통과하고, 실제 라우트와 맞고,
build-image.sh가 그 내용을 라벨로 단다. 실제 docker 빌드는 smoke.sh가 한다(가짜 DOCKER로 라벨 인자만 본다)."""
import json
import os
import pathlib
import re
import subprocess
import sys

import boundary_contracts as bc
import pytest

from conftest import example_text
from question_authoring.components.link_authoring_server import Api
from question_authoring.wiring.service import Actor

ROOT = pathlib.Path(__file__).resolve().parent.parent
DECL = json.loads((ROOT / "deploy-exposure.json").read_text(encoding="utf-8"))
H = {"Authorization": "Bearer tok-a"}
SAMPLE = {"*": "dr-x"}   # 경로의 '*'는 이 값으로 바꿔 부른다


def test_declaration_passes_the_contract():
    assert not list(bc.validator("Exposure").iter_errors(DECL))
    assert DECL["service"] == "question-authoring"


def exposed():
    return [(m, p) for r in DECL["routes"] if r["auth"]["kind"] != "denied" for p in r["paths"] for m in r.get("methods") or []]


def denied_paths():
    return [p for r in DECL["routes"] if r["auth"]["kind"] == "denied" for p in r["paths"]]


@pytest.fixture
def api(svc):
    return Api(svc, {"tok-a": Actor("dr-kim", "question-author")}, read_tokens={"rd": "synthetic-data"})


# Caddy의 '/*'는 뒤의 모든 경로를 뜻한다. 코드가 서비스하는 구체 경로로 펼쳐 본다
GLOBS = {"/v1/drafts/*": ["/v1/drafts/dr-x", "/v1/drafts/dr-x/preview", "/v1/drafts/dr-x/edits", "/v1/drafts/dr-x/acknowledge",
                          "/v1/drafts/dr-x/confirm", "/v1/drafts/dr-x/discard"],
         "/v1/question-sets/*": ["/v1/question-sets/qs-main/latest"]}


def concrete(path):
    return GLOBS.get(path, [path])


def call(api, method, path, body=b"{}"):
    return api.handle(method, path, H, body)


def served(api, method, path):
    """코드가 이 메서드·경로를 아는가: '없는 경로' 404 NOT_FOUND가 아니면 안다(없는 초안 404 같은 업무 오류는 아는 경로)."""
    st, out = call(api, method, path)
    return not (st == 405 or (st == 404 and out.get("message", "") in ("경로가 없다",) or "없는 경로" in out.get("message", "")))


def test_every_declared_exposed_route_exists_in_code(api):
    assert exposed()
    for m, p in exposed():
        assert any(served(api, m, c) for c in concrete(p)), (m, p)


def test_methods_not_declared_are_not_served_by_code(api):
    for r in DECL["routes"]:
        if r["auth"]["kind"] == "denied":
            continue
        for p in r["paths"]:
            for m in {"GET", "POST", "PUT", "PATCH", "DELETE"} - set(r.get("methods") or []):
                assert not any(served(api, m, c) for c in concrete(p)), f"코드는 {m} {p}를 서비스하는데 선언에 없다"


def test_roles_are_the_gateway_roles_that_reach_the_author_routes():
    roles = {tuple(sorted(r["auth"]["roles"])) for r in DECL["routes"] if r["auth"]["kind"] == "account"}
    assert roles == {("author", "clinical-reviewer")}


def test_every_known_service_path_is_declared_exposed_or_denied():
    """코드가 서비스하는 경로 묶음이 선언에 빠지지 않는다(새 경로를 만들면 이 시험이 깨져 선언을 고치게 한다)."""
    src = (ROOT / "question_authoring" / "components" / "link_authoring_server.py").read_text(encoding="utf-8")
    known = set(re.findall(r'R\("[A-Z]+", "(/v1/[a-z-]+)', src)) | set(re.findall(r'sc = "(/v1/[a-z-]+)"', src))
    assert {"/v1/drafts", "/v1/materials", "/v1/reviews", "/v1/signals", "/v1/question-sets"} <= known, known
    declared = {p.rstrip("/*").rstrip("/") or p for r in DECL["routes"] for p in r["paths"]}
    assert known <= declared, f"선언에 없는 서비스 경로: {sorted(known - declared)}"


def test_i1_read_surface_and_signals_are_denied_at_the_gateway():
    d = denied_paths()
    assert "/v1/question-sets" in d and "/v1/question-sets/*" in d and "/v1/signals" in d


def test_build_image_attaches_the_declaration_as_label(tmp_path):
    """가짜 docker가 받은 --label 값이 정본 파일의 내용과 같고 계약 Exposure를 통과한다."""
    from test_build_image import FAKE_DOCKER, SHA, make_tree, write_lock
    src = tmp_path / "src"
    src.mkdir()
    (src / "Dockerfile").write_text("FROM scratch\n")
    (src / "deploy-exposure.json").write_text((ROOT / "deploy-exposure.json").read_text(encoding="utf-8"))
    write_lock(src)
    req = {"source_dir": str(src), "revision": "abcdef1", "versions": {"boundary": "v3.1.1", "survey_schema": "v3.0.0"},
           "dependency_sources": {"boundary": make_tree(tmp_path / "bc", "boundary_contracts", "3.1.1"), "survey_schema": make_tree(tmp_path / "ss", "openit_survey_schema", "3.0.0")},
           "image_tag": "question-authoring-test/q:t1"}
    fake = tmp_path / "docker"
    fake.write_text(FAKE_DOCKER.replace("__SHA__", SHA))
    fake.chmod(0o755)
    (tmp_path / "req.json").write_text(json.dumps(req))
    log = tmp_path / "log.txt"
    from test_build_image import OFFLINE_WHEEL
    p = subprocess.run(["bash", str(ROOT / "scripts" / "build-image.sh"), str(tmp_path / "req.json")], capture_output=True, text=True,
                       env=dict(os.environ, DOCKER=str(fake), PYTHON=sys.executable, FAKE_LOG=str(log), OPENIT_LINK_SPEC=make_tree(tmp_path / "lk", "openit_link", "0.1.0"), **OFFLINE_WHEEL), timeout=120)
    assert p.returncode == 0, p.stderr
    m = re.search(r"--label io\.openit\.exposure=(\{.*?\}) --", log.read_text())
    assert m, log.read_text()[:400]
    label = json.loads(m.group(1))
    assert label == DECL and not list(bc.validator("Exposure").iter_errors(label))
