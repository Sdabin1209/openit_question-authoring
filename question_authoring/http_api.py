"""저작 API의 HTTP 계층(얇게). 로직은 service.py에 있다. 관리자 콘솔은 이 API의 클라이언트로만 붙는다(D08).

handle()은 소켓 없이 시험할 수 있는 순수 함수 형태이고, make_server()가 표준 라이브러리 서버로 감싼다.
인증은 `Authorization: Bearer <토큰>`이고 토큰 -> (actor id, 역할) 표는 서비스 진입점이 환경 변수에서 만들어 넘긴다.
"""
from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Mapping, Optional
from urllib.parse import parse_qs, urlparse

from boundary_contracts.errors import BoundaryError

from .service import Actor, AuthoringService

STATUS = {"NOT_FOUND": 404, "INVALID_RECORD": 400, "UNSUPPORTED_VERSION": 400, "CONFLICT": 409,
          "INTEGRITY_ERROR": 500, "FAILED_PRECONDITION": 412, "PERMISSION_DENIED": 403, "UNAVAILABLE": 503}
MAX_BODY = 8 * 1024 * 1024


def _json(body: bytes) -> dict:
    try:
        v = json.loads(body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BoundaryError("INVALID_RECORD", "JSON 본문이 아니다")
    if not isinstance(v, dict):
        raise BoundaryError("INVALID_RECORD", "JSON 객체가 필요하다")
    return v


def _need(d: dict, key: str):
    if key not in d:
        raise BoundaryError("INVALID_RECORD", f"필수 항목이 없다: {key}")
    return d[key]


class Api:
    def __init__(self, service: AuthoringService, tokens: Mapping[str, Actor], *, ready: Callable[[], bool] = lambda: True):
        self.svc = service
        self.tokens = dict(tokens)
        self.ready = ready

    def handle(self, method: str, url: str, headers: Mapping[str, str], body: bytes = b"") -> tuple[int, dict]:
        u = urlparse(url)
        path, q = u.path, {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if method == "GET" and path == "/healthz":
                return 200, {"status": "ok"}
            if method == "GET" and path == "/readyz":
                ok = self.ready()
                return (200 if ok else 503), {"status": "ready" if ok else "not-ready"}
            actor = self._actor(headers)
            return 200, self._route(method, path, q, headers, body, actor)
        except BoundaryError as e:
            return STATUS[e.code], e.to_record()

    def _actor(self, headers) -> Actor:
        auth = {k.lower(): v for k, v in headers.items()}.get("authorization", "")
        m = re.match(r"^Bearer (\S+)$", auth)
        actor = self.tokens.get(m.group(1)) if m else None
        if actor is None:
            raise BoundaryError("PERMISSION_DENIED", "인증이 필요하다")
        return actor

    def _route(self, method, path, q, headers, body, actor):
        s = self.svc
        if method == "POST" and path == "/v1/drafts":
            b = _json(body)
            return s.create_draft(actor, _need(b, "question_set_id"), base_version=b.get("base_version", "latest"))
        if method == "GET" and path == "/v1/drafts":
            return {"drafts": s.list_drafts(q.get("question_set_id"))}
        if method == "POST" and path == "/v1/materials":
            return s.ingest_material(actor, _need(q, "question_set_id"), _need(q, "filename"), body,
                                     base_version=q.get("base_version", "latest"), extractor=q.get("extractor"),
                                     accept_phi_warning=q.get("accept_phi_warning") == "true")
        if method == "POST" and path == "/v1/reviews":
            b = _json(body)
            return s.add_review(actor, _need(b, "question_set"), decision=_need(b, "decision"),
                                clinical_basis_approved=_need(b, "clinical_basis_approved"),
                                reason=b.get("reason"), review_id=b.get("review_id"))
        m = re.match(r"^/v1/drafts/([^/]+)(?:/(preview|edits|acknowledge|confirm|discard))?$", path)
        if m:
            did, act = m.groups()
            if method == "GET" and act is None:
                return s.get_draft(did)
            if method == "GET" and act == "preview":
                return s.get_preview(did)
            b = _json(body) if method == "POST" else {}
            if method == "POST" and act == "edits":
                return s.apply_edits(actor, did, _need(b, "edits"))
            if method == "POST" and act == "acknowledge":
                return s.acknowledge_flags(actor, did, _need(b, "flag_ids"))
            if method == "POST" and act == "confirm":
                return s.confirm(actor, did, _need(b, "preview_hash"))
            if method == "POST" and act == "discard":
                return s.discard_draft(actor, did, _need(b, "reason"))
        raise BoundaryError("NOT_FOUND", f"없는 경로: {method} {path}")


def make_server(api: Api, host: str, port: int) -> ThreadingHTTPServer:
    class H(BaseHTTPRequestHandler):
        def _do(self, method):
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                status, out = 413, BoundaryError("INVALID_RECORD", "본문이 너무 크다").to_record()
            else:
                status, out = api.handle(method, self.path, dict(self.headers), self.rfile.read(n) if n else b"")
            data = json.dumps(out, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self): self._do("GET")
        def do_POST(self): self._do("POST")

        def log_message(self, fmt, *args):  # 표준 출력으로 낸다(개인 정보 없는 요청 줄만)
            print("%s %s" % (self.address_string(), fmt % args), flush=True)

    return ThreadingHTTPServer((host, port), H)
