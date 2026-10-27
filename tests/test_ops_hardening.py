"""전수 점검 P6(운영 구성 거부), P7(자원 한도, SIGTERM), P4/D88 U4(보존·정리), M-6(v2 Trace 문항 ID)."""
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from conftest import example_text
from helpers import AUTHOR
from question_authoring.__main__ import build, main, purge_once
from question_authoring.http_api import Api, make_server
from question_authoring.service import Actor, AuthoringService
from question_authoring.signals import question_ids_from_trace
from question_authoring.store import MemoryQuestionStore, SqliteQuestionStore


# ---- P6 ----
def test_production_refuses_to_start_without_db_url_or_tokens(monkeypatch, tmp_path):
    monkeypatch.delenv("QA_DEV_MODE", raising=False)
    for k in ("QA_DATABASE_URL", "QA_DATABASE_URL_FILE", "QA_AUTHOR_TOKENS", "QA_AUTHOR_TOKENS_FILE", "QA_GATEWAY_TOKENS", "QA_GATEWAY_TOKENS_FILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("QA_DB_PATH", str(tmp_path / "q.db"))
    with pytest.raises(SystemExit) as e:
        build()
    assert "QA_DATABASE_URL" in str(e.value)
    monkeypatch.setenv("QA_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/d")     # URL이 있어도 토큰이 없으면 거부(DB 접속 전에 판단)
    with pytest.raises(SystemExit) as e:
        build()
    assert "토큰" in str(e.value)
    assert not (tmp_path / "q.db").exists()                                      # 조용히 SQLite를 만들지 않았다


def test_dev_mode_allows_sqlite_and_no_tokens(monkeypatch, tmp_path):
    monkeypatch.setenv("QA_DB_PATH", str(tmp_path / "q.db"))
    monkeypatch.delenv("QA_DATABASE_URL", raising=False)
    api, store = build()
    assert api.handle("GET", "/readyz", {})[0] == 200
    store.close()


def test_roles_and_two_person_flag_are_validated(monkeypatch, tmp_path):
    monkeypatch.setenv("QA_DB_PATH", str(tmp_path / "q.db"))
    monkeypatch.setenv("QA_AUTHOR_TOKENS", "t:dr-kim:superuser")
    with pytest.raises(SystemExit):
        build()
    monkeypatch.setenv("QA_AUTHOR_TOKENS", "t:dr-kim:question-author")
    monkeypatch.setenv("QA_REQUIRE_TWO_PERSON", "maybe")
    with pytest.raises(SystemExit):
        build()
    monkeypatch.setenv("QA_REQUIRE_TWO_PERSON", "false")
    api, store = build()
    st, body = api.handle("GET", "/readyz", {})
    assert st == 200 and body["warnings"] == ["two_person_disabled"]
    store.close()


# ---- P7 ----
def raw(port, data, wait=3):
    s = socket.create_connection(("127.0.0.1", port), timeout=wait)
    s.sendall(data)
    out = b""
    try:
        while True:
            c = s.recv(65536)
            if not c:
                break
            out += c
    except socket.timeout:
        out += b"<TIMEOUT>"
    s.close()
    return out


def test_unauthenticated_body_is_not_read_and_concurrency_is_capped(svc):
    api = Api(svc, {"tok": AUTHOR})
    srv = make_server(api, "127.0.0.1", 0, request_timeout=5, max_concurrent=1)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        t = time.monotonic()
        # 인증 없이 8MiB를 보내겠다고 하고 본문은 보내지 않는다: 본문을 기다리지 않고 바로 403
        out = raw(port, b"POST /v1/materials?question_set_id=q&filename=f.txt HTTP/1.1\r\nHost: x\r\nContent-Length: 8000000\r\n\r\n")
        assert b" 403 " in out.split(b"\r\n")[0] and time.monotonic() - t < 2
        # 요청 하나가 자리를 쥐고(본문을 기다리는 중) 있으면 다음 인증된 요청은 503 + Retry-After, healthz는 그대로 응답
        holder = socket.create_connection(("127.0.0.1", port), timeout=5)
        holder.sendall(b"POST /v1/drafts HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer tok\r\nContent-Length: 100\r\n\r\n")
        time.sleep(0.5)
        out = raw(port, b"GET /v1/drafts HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer tok\r\n\r\n")
        assert b" 503 " in out.split(b"\r\n")[0] and b"Retry-After" in out
        assert urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3).status == 200
        holder.close()
        time.sleep(0.5)
        out = raw(port, b"GET /v1/drafts HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer tok\r\nConnection: close\r\n\r\n")
        assert b" 200 " in out.split(b"\r\n")[0]
    finally:
        srv.shutdown(); srv.server_close()


def test_sigterm_stops_the_server_promptly(tmp_path):
    env = dict(os.environ, QA_DEV_MODE="1", QA_DB_PATH=str(tmp_path / "q.db"), QA_PORT="0", QA_HOST="127.0.0.1")
    p = subprocess.Popen([sys.executable, "-m", "question_authoring"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        line = p.stdout.readline()
        assert "시작" in line
        time.sleep(0.3)
        p.send_signal(signal.SIGTERM)
        assert p.wait(timeout=10) == 0
    finally:
        if p.poll() is None:
            p.kill()


# ---- P4 / D88 U4 ----
def _old(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(params=["memory", "sqlite"])
def store2(request):
    s = MemoryQuestionStore() if request.param == "memory" else SqliteQuestionStore()
    yield s
    s.close()


def test_retention_purge(store2, validators, monkeypatch):
    s = AuthoringService(store2, validators, require_two_person=False)
    k = AUTHOR
    confirmed = s.ingest_material(k, "qs-a", "f.txt", example_text())["draft_id"]
    s.confirm(k, confirmed, s.get_preview(confirmed)["preview_hash"])           # 버전이 이 원본을 참조한다
    old_open = s.ingest_material(k, "qs-b", "f.txt", example_text().replace("체온".encode(), "혈압".encode()))["draft_id"]
    fresh = s.create_draft(k, "qs-c")["draft_id"]
    orphan_hash = "sha256:" + "e" * 64
    store2.put_source(orphan_hash, "orphan.txt", b"x")                            # 어디에도 참조되지 않는 원본
    # 오래된 것처럼 만든다
    d = store2.load_draft(old_open); d["updated_at"] = _old(40); store2.save_draft(d)
    if hasattr(store2, "_blob_times"):
        store2._blob_times = {h: _old(40) for h in store2._blob_times}
    else:
        store2._db.execute("UPDATE blobs SET created_at=?", (_old(40),))
    out = purge_once(store2)
    assert out["drafts_removed"] == 1 and store2.load_draft(old_open) is None
    assert store2.load_draft(fresh) is not None and store2.load_draft(confirmed) is not None   # 최근 초안과 확정된 초안은 남는다
    assert store2.get_source(orphan_hash) is None                                  # 고아 원본은 정리
    h = s.get_draft(confirmed)["source"]["source_hash"]
    assert store2.get_source(h) is not None                                        # 버전이 참조하는 원본은 남는다
    assert len(store2.reader().list_versions("qs-a")[0]) == 1                      # 버전·검수는 지우지 않는다
    assert purge_once(store2) == {"drafts_removed": 0, "sources_removed": 0}       # 멱등


def test_purge_stamps_unknown_age_and_waits(store2):
    store2.put_source("sha256:" + "f" * 64, "f", b"x")
    if hasattr(store2, "_blob_times"):
        store2._blob_times["sha256:" + "f" * 64] = None
    else:
        store2._db.execute("UPDATE blobs SET created_at=NULL")
    assert purge_once(store2)["sources_removed"] == 0                              # 나이를 모르면 이번에 시각을 적고 기간이 지난 뒤 지운다
    assert store2.get_source("sha256:" + "f" * 64) is not None


def test_retention_rejects_indefinite(monkeypatch):
    for bad in ("0", "-1", "none", "null", "abc"):
        monkeypatch.setenv("QA_DRAFT_RETENTION_DAYS", bad)
        with pytest.raises(SystemExit):
            purge_once(MemoryQuestionStore())


# ---- M-6 ----
def test_question_ids_from_v2_trace():
    v2 = {"session_id": "S", "turn_id": "1", "extract_input": {"question_id": "Q4", "current_question": "?", "user_utterance": "<MASKED>", "state": {}},
          "extract_output": {"values": [], "action": "NEXT", "next_item": "Q5"}}
    assert question_ids_from_trace(v2) == {"Q4"}
    assert question_ids_from_trace({"extract_input": None, "extract_output": None}) == set()
    assert question_ids_from_trace({"extract_input": {"question_id": ""}}) == set()
