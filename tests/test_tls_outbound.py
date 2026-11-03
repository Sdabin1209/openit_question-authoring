"""U99: 바깥 호출의 TLS 설정. (1) 이웃 주소는 설정으로만 들어온다 (2) 인증서 검증을 끄는 코드가 없다 (3) 사설 루트는 표준 SSL_CERT_FILE만으로 믿는다
(4) DB 접속의 sslmode 등은 접속 문자열 그대로 간다. TLS 종단은 deploy의 사이드카가 한다(이 서비스는 TLS 서버가 아니다)."""
import json
import re
import shutil
import ssl
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "question_authoring"
CODE = [p for p in SRC.rglob("*.py") if "types" not in p.relative_to(SRC).parts]


def test_no_fixed_neighbor_address_in_code():
    """코드에 http:// 주소나 localhost·고정 IP가 없다(주석·문서 문자열 제외 아님: 줄 전체를 본다). 스키마 $id 기반 URL(https://openit.local/schemas/)은 식별자일 뿐 호출 대상이 아니다."""
    bad = []
    for p in CODE:
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            # 0.0.0.0은 받는 쪽 바인드 주소(QA_HOST 기본)라 이웃 주소가 아니다
            if re.search(r"http://|localhost|\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", line.replace("0.0.0.0", "")):
                bad.append(f"{p.name}:{n}: {line.strip()[:80]}")
    assert bad == []


def test_no_code_turns_certificate_verification_off():
    pat = re.compile(r"verify\s*=\s*False|CERT_NONE|check_hostname\s*=\s*False|_create_unverified_context|sslmode\s*[=:]\s*['\"]?(disable|allow|prefer)")
    bad = [f"{p.name}:{n}" for p in CODE for n, l in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if pat.search(l)]
    assert bad == []


def test_postgres_params_keep_ssl_settings_from_the_connection_string():
    from question_authoring.components.store_postgres import PostgresQuestionStore
    from question_authoring.store.migrate import migrate_postgres  # noqa: F401
    st = object.__new__(PostgresQuestionStore)
    st._url = "postgresql://u:p@h:5432/d?sslmode=verify-full&sslrootcert=/run/secrets/root.crt"
    st._schema, st._read_only = None, False
    p = st._params(3, 3)
    assert p["sslmode"] == "verify-full" and p["sslrootcert"] == "/run/secrets/root.crt"


def test_migration_uri_keeps_sslmode(monkeypatch):
    from question_authoring.store import migrate
    seen = {}
    monkeypatch.setattr(migrate, "_apply", lambda uri, dialect, lock: seen.update(uri=uri))
    migrate.migrate_postgres({"user": "u", "password": "p", "host": "h", "port": 5432, "dbname": "d", "sslmode": "verify-full", "sslrootcert": "/r.crt"})
    assert "sslmode=verify-full" in seen["uri"] and "sslrootcert=%2Fr.crt" in seen["uri"]


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl 명령이 없다")
def test_private_root_is_trusted_through_ssl_cert_file_only(tmp_path):
    d = tmp_path
    run = lambda *a: subprocess.run(a, cwd=d, check=True, capture_output=True, timeout=60)
    run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "root.key", "-out", "root.crt", "-days", "1", "-subj", "/CN=test-root",
        "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign")
    run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", "srv.key", "-out", "srv.csr", "-subj", "/CN=localhost")
    (d / "ext.cnf").write_text("subjectAltName=DNS:localhost\nbasicConstraints=CA:FALSE\n")
    run("openssl", "x509", "-req", "-in", "srv.csr", "-CA", "root.crt", "-CAkey", "root.key", "-CAcreateserial", "-out", "srv.crt", "-days", "1", "-extfile", "ext.cnf")

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            b = json.dumps({"items": [], "next_cursor": None}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(d / "srv.crt", d / "srv.key")
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        code = ("import sys\nfrom question_authoring.components.link_failures_read import http_failure_source\n"
                f"s = http_failure_source('https://localhost:{srv.server_address[1]}', 't', 5.0)\n"
                "try:\n    print('OK', s.list_cases(limit=1))\nexcept Exception as e:\n    print('ERR', getattr(e, 'code', type(e).__name__))\n")
        env = {"PATH": "/usr/bin:/bin", "HOME": str(d)}
        # 클라이언트는 별도 프로세스: 표준 환경 변수만 바꿔 그 효과를 본다. 검증은 켜진 채다
        without = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60, env=env, cwd=d)
        with_root = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60,
                                   env={**env, "SSL_CERT_FILE": str(d / "root.crt")}, cwd=d)
    finally:
        srv.shutdown(); srv.server_close(); th.join(10)
    assert without.stdout.startswith("ERR"), without.stdout + without.stderr          # 루트를 모르면 검증이 실패한다(검증이 꺼져 있지 않다)
    assert with_root.stdout.startswith("OK"), with_root.stdout + with_root.stderr     # SSL_CERT_FILE만으로 사설 루트를 믿는다
