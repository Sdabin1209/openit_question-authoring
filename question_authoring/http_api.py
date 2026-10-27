"""저작 API의 HTTP 계층(얇게). 로직은 service.py에 있다. 관리자 콘솔은 이 API의 클라이언트로만 붙는다(D08).

handle()은 소켓 없이 시험할 수 있는 순수 함수 형태이고, make_server()가 표준 라이브러리 서버로 감싼다.
인증은 `Authorization: Bearer <토큰>`이고 토큰 -> (actor id, 역할) 표는 서비스 진입점이 환경 변수에서 만들어 넘긴다.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlparse

from boundary_contracts.errors import BoundaryError

from .service import Actor, AuthoringService

STATUS = {"NOT_FOUND": 404, "INVALID_RECORD": 400, "UNSUPPORTED_VERSION": 422, "CONFLICT": 409,
          "INTEGRITY_ERROR": 422, "FAILED_PRECONDITION": 412, "PERMISSION_DENIED": 403, "UNAVAILABLE": 503}
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
    def __init__(self, service: AuthoringService, tokens: Mapping[str, Actor], *,
                 read_tokens: Optional[Mapping[str, str]] = None, signals=None, ready: Callable[[], bool] = lambda: True,
                 db_kind: Optional[str] = None, signals_deadline: Optional[float] = 20.0,
                 gateway_tokens: Optional[Iterable[str]] = None, user_roles: Optional[Mapping[str, str]] = None,
                 warnings: Optional[list] = None):
        self.signals_deadline = signals_deadline
        # gateway 경유 인증(D88 E2 준비): gateway가 사람 인증을 마치고 서비스 토큰(gateway_tokens)과 X-Openit-User를 붙인다.
        # 사람의 역할은 이 서비스의 user_roles(사용자 ID -> 역할) 표가 정한다. 헤더를 믿는 것은 gateway 토큰을 가진 호출뿐이다.
        self.gateway_tokens = frozenset(gateway_tokens or ())
        self.user_roles = dict(user_roles or {})
        self.warnings = list(warnings or [])
        self.svc = service
        self.tokens = dict(tokens)
        self.read_tokens = dict(read_tokens or {})    # 토큰 -> 역할(읽기 전용)
        self.signals = signals                         # FailureSource | None (I7 미설정이면 None)
        self.ready = ready
        self.db_kind = db_kind                         # 'sqlite' | 'postgres' (종류만. 접속 정보는 절대 쓰지 않는다)

    def handle(self, method: str, url: str, headers: Mapping[str, str], body: bytes = b"") -> tuple[int, dict]:
        u = urlparse(url)
        path, q = u.path, {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if method == "GET" and path == "/healthz":
                return 200, {"status": "ok"}
            if method == "GET" and path == "/readyz":
                ok = self.ready()
                body = {"status": "ready" if ok else "not-ready"}
                if self.db_kind:
                    body["db"] = self.db_kind
                if self.warnings:
                    body["warnings"] = list(self.warnings)     # 200을 막지 않는다(경고만)
                return (200 if ok else 503), body
            if path == "/v1/question-sets" or path.startswith("/v1/question-sets/"):
                self._reader_auth(headers)          # 읽기 토큰 또는 저작 토큰
                if method != "GET":
                    raise BoundaryError("NOT_FOUND", f"없는 경로: {method} {path}")
                return 200, self._read(path, q)
            actor = self._actor(headers)            # 저작 경로: 읽기 토큰은 여기서 403
            return 200, self._route(method, path, q, headers, body, actor)
        except BoundaryError as e:
            return STATUS[e.code], e.to_record()
        except Exception as e:  # 예상 밖의 오류. 연결을 끊지 않고 JSON 500으로 답한다. 원문 메시지는 쓰지 않는다(접속 정보·자료가 섞일 수 있다)
            print(f"내부 오류: {type(e).__name__}", flush=True)
            return 500, BoundaryError("UNAVAILABLE", "내부 오류").to_record()

    def _bearer(self, headers) -> Optional[str]:
        auth = {k.lower(): v for k, v in headers.items()}.get("authorization", "")
        m = re.match(r"^Bearer (\S+)$", auth)
        return m.group(1) if m else None

    def _reader_auth(self, headers) -> None:
        tok = self._bearer(headers)
        if tok is None or (tok not in self.read_tokens and self._principal(headers) is None):
            raise BoundaryError("PERMISSION_DENIED", "인증이 필요하다")

    def _page(self, q) -> tuple[Optional[str], int]:
        try:
            limit = int(q.get("limit", 100))
        except ValueError:
            raise BoundaryError("INVALID_RECORD", "limit은 정수여야 한다")
        if not 1 <= limit <= 500:
            raise BoundaryError("INVALID_RECORD", "limit은 1~500이어야 한다")
        return q.get("cursor"), limit

    def _read(self, path: str, q: dict):
        """I1 QuestionReader의 HTTP 읽기 면(boundary-contracts/HTTP.md). 응답은 스키마 검증을 거친다."""
        r = self.svc.store.reader()
        v = self.svc.v
        if path == "/v1/question-sets":
            cur, lim = self._page(q)
            items, nxt = r.list_ids(cursor=cur, limit=lim)
            return {"items": items, "next_cursor": nxt}
        m = re.match(r"^/v1/question-sets/([^/]+)/(versions|latest)$", path)
        if m:
            qsid, what = m.groups()
            if what == "latest":
                flag = q.get("reviewed_only", "false")
                if flag not in ("true", "false"):
                    raise BoundaryError("INVALID_RECORD", "reviewed_only는 true 또는 false")
                return r.latest(qsid, reviewed_only=flag == "true")
            cur, lim = self._page(q)
            items, nxt = r.list_versions(qsid, cursor=cur, limit=lim)
            return {"items": items, "next_cursor": nxt}
        m = re.match(r"^/v1/question-sets/([^/]+)/versions/([^/]+)(?:/(reviews|change))?$", path)
        if m:
            qsid, ver, sub = m.groups()
            ref = self.svc.store.ref_of(qsid, ver)
            if sub is None:
                bank = r.get_question_bank(ref)      # 해시 재계산 포함
                v.check("QuestionBank", bank)
                return bank
            if sub == "reviews":
                revs = r.get_reviews(ref)
                for x in revs:
                    v.check("ReviewRecord", x)
                return {"items": revs, "next_cursor": None}
            ch = r.get_change(ref)
            if ch is not None:
                v.check("ChangeClassification", ch)
            return {"item": ch}
        raise BoundaryError("NOT_FOUND", f"없는 경로: GET {path}")

    def _principal(self, headers) -> Optional[Actor]:
        h = {k.lower(): v for k, v in headers.items()}
        m = re.match(r"^Bearer (\S+)$", h.get("authorization", ""))
        if not m:
            return None
        tok = m.group(1)
        if tok in self.tokens:                      # 정적 토큰: X-Openit-User는 무시한다(클라이언트가 보낸 값을 믿지 않는다)
            return self.tokens[tok]
        if tok in self.gateway_tokens:
            user = h.get("x-openit-user", "")
            role = self.user_roles.get(user)
            if role is None or not re.match(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$", user):
                return None                         # 헤더가 없거나 이 서비스가 아는 사용자가 아니다
            return Actor(user, role)
        return None

    def has_credentials(self, headers) -> bool:
        """본문을 읽기 전의 빠른 검사: 알려진 토큰(또는 읽기 토큰)인가. 8MiB를 받은 뒤에야 거절하지 않으려는 것이다."""
        if self._principal(headers) is not None:
            return True
        m = re.match(r"^Bearer (\S+)$", {k.lower(): v for k, v in headers.items()}.get("authorization", ""))
        return bool(m and m.group(1) in self.read_tokens)

    def _actor(self, headers) -> Actor:
        actor = self._principal(headers)
        if actor is None:
            raise BoundaryError("PERMISSION_DENIED", "인증이 필요하다")
        return actor

    def _route(self, method, path, q, headers, body, actor):
        s = self.svc
        if method == "GET" and path == "/v1/signals":
            s.require_reader(actor)                    # 저자 또는 검수자. 읽기 토큰은 여기 오기 전에 403
            if self.signals is None:
                return {"status": "DISABLED", "reason": "I7(ops-feedback)가 설정되지 않았다(QA_FAILURES_URL)"}
            try:
                mx = int(q.get("max_cases", 2000))
            except ValueError:
                raise BoundaryError("INVALID_RECORD", "max_cases는 정수여야 한다")
            if not 1 <= mx <= 20000:
                raise BoundaryError("INVALID_RECORD", "max_cases는 1~20000이어야 한다")
            from .signals import build_signals
            try:
                return build_signals(self.signals, question_set_id=q.get("question_set_id"),
                                     review_status=q.get("review_status"), max_cases=mx,
                                     deadline_seconds=self.signals_deadline)
            except BoundaryError as e:
                if e.code in ("INTEGRITY_ERROR", "INVALID_RECORD", "UNSUPPORTED_VERSION"):   # 상류의 깨진 응답은 성공으로 취급하지 않는다(FAULT_POLICY 10)
                    raise BoundaryError("UNAVAILABLE", "ops-feedback의 응답이 올바르지 않다") from None
                raise
        if method == "POST" and path == "/v1/drafts":
            b = _json(body)
            return s.create_draft(actor, _need(b, "question_set_id"), base_version=b.get("base_version", "latest"))
        if method == "GET" and path == "/v1/drafts":
            s.require_reader(actor)
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
                s.require_reader(actor)
                return s.get_draft(did)
            if method == "GET" and act == "preview":
                s.require_reader(actor)
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


def make_server(api: Api, host: str, port: int, request_timeout: Optional[float] = 30.0,
                max_concurrent: int = 32) -> ThreadingHTTPServer:
    # 동시에 처리하는 요청 수의 상한(8MiB 본문 x N이 메모리를 채우지 않게). /healthz, /readyz는 세지 않는다.
    # 기본 32: 근거 기록 없음(저자는 관리자 소수라는 가정에서 넉넉하게 잡은 값, 측정 안 함).
    slots = threading.BoundedSemaphore(max_concurrent)

    class H(BaseHTTPRequestHandler):
        timeout = request_timeout   # 소켓 읽기·쓰기가 이 시간 멈추면 끊는다. 멈춘 업로드가 스레드를 영원히 쥐지 못하게 한다

        def _reply(self, status, out, extra=None):
            data = json.dumps(out, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _do(self, method):
            path = self.path.split("?", 1)[0]
            if path in ("/healthz", "/readyz"):
                return self._do_inner(method)
            if not api.has_credentials(dict(self.headers)):
                self.close_connection = True    # 인증 없는 호출의 본문은 읽지 않는다
                return self._reply(403, BoundaryError("PERMISSION_DENIED", "인증이 필요하다").to_record())
            if not slots.acquire(blocking=False):
                self.close_connection = True
                return self._reply(503, BoundaryError("UNAVAILABLE", "동시 요청이 너무 많다. 잠시 뒤 다시 시도").to_record(),
                                   {"Retry-After": "2"})
            try:
                return self._do_inner(method)
            finally:
                slots.release()

        def _do_inner(self, method):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n < 0:
                    raise ValueError
            except ValueError:
                n, bad = 0, True
            else:
                bad = False
            body = b""
            if bad:
                status, out = 400, BoundaryError("INVALID_RECORD", "Content-Length가 올바르지 않다").to_record()
            elif n > MAX_BODY:
                status, out = 413, BoundaryError("INVALID_RECORD", "본문이 너무 크다").to_record()
                self.close_connection = True   # 읽지 않은 본문이 남는다
            else:
                try:
                    body = self.rfile.read(n) if n else b""
                except (OSError, ValueError):  # 읽는 중 멈춤(timeout)·끊김: 반쯤 온 자료는 처리하지 않는다
                    self.close_connection = True
                    return
                if len(body) < n:              # 업로드 도중 연결이 끊김: 처리하지 않는다(저장 없음)
                    self.close_connection = True
                    return
                status, out = api.handle(method, self.path, dict(self.headers), body)
            self._reply(status, out)

        def do_GET(self): self._do("GET")
        def do_POST(self): self._do("POST")

        def log_message(self, fmt, *args):  # 표준 출력으로 낸다(개인 정보 없는 요청 줄만)
            print("%s %s" % (self.address_string(), fmt % args), flush=True)

    return ThreadingHTTPServer((host, port), H)
