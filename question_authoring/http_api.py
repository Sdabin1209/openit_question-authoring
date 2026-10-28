"""저작 API의 HTTP 계층(얇게). 로직은 service.py에 있다. 관리자 콘솔은 이 API의 클라이언트로만 붙는다(D08).

handle()은 소켓 없이 시험할 수 있는 순수 함수 형태이고, wsgi_app()이 WSGI 앱으로, make_server()가 waitress로 감싼다.
인증은 `Authorization: Bearer <토큰>`이고 토큰 -> (actor id, 역할) 표는 서비스 진입점이 환경 변수에서 만들어 넘긴다.
"""
from __future__ import annotations

import hmac
import json
import re
import time
from http import HTTPStatus
from typing import Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, unquote, urlparse

from boundary_contracts.errors import BoundaryError
from waitress import create_server

from . import contract
from .service import Actor, AuthoringService

STATUS = {"NOT_FOUND": 404, "INVALID_RECORD": 400, "UNSUPPORTED_VERSION": 422, "CONFLICT": 409,
          "INTEGRITY_ERROR": 422, "FAILED_PRECONDITION": 412, "PERMISSION_DENIED": 403, "UNAVAILABLE": 503}


class AuthError(BoundaryError):
    """인증/인가 거절. 본문 code는 어느 쪽이든 PERMISSION_DENIED, HTTP 상태만 401(자격 없음·틀림) 또는 403(자격은 맞으나 권한 없음).
    왜: 계약 CONVENTIONS.md 6절(D106): RFC 9110 15.5.2(401은 유효한 자격이 없음, 403은 이해했으나 거부)·RFC 6750 3절(Bearer 도전)."""

    def __init__(self, status: int, message: str):
        super().__init__("PERMISSION_DENIED", message)
        self.status = status


def _json(body: bytes) -> dict:
    try:
        v = json.loads(body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BoundaryError("INVALID_RECORD", "JSON 본문이 아니다")
    if not isinstance(v, dict):
        raise BoundaryError("INVALID_RECORD", "JSON 객체가 필요하다")
    return v


def _body(def_name: str, body: bytes) -> dict:
    """요청 본문을 읽고 계약 AuthoringRequests의 $defs로 검사한다(필수 키, 형식, 모르는 키 거부). 이 뒤의 코드는 키가 있다고 믿는다(R7)."""
    b = _json(body)
    contract.check_request(def_name, b)
    return b


# gateway 사용자 ID: 일반 ID 또는 이메일형(local@domain). 문자 집합과 길이를 제한한다(총 길이가 계약 Id의 128자 안에 들도록 local 60, domain 60).
_USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}(?:@[A-Za-z0-9][A-Za-z0-9.-]{0,59})?$")


def _lookup(table: Mapping, token: str):
    """토큰 표 조회. 모든 후보를 `hmac.compare_digest`로 끝까지 비교한다(조기 종료·dict 해시 조회 없음).
    왜: 문자열 `==`/dict 조회는 앞부분이 맞을수록 오래 걸려 타이밍 차이로 토큰을 추정할 수 있다. Python hmac 문서와 OWASP가 상수 시간 비교를 권한다(ADR 0010 개정).
    바이트로 비교하는 이유: compare_digest는 ASCII가 아닌 str에 TypeError를 내기 때문이다."""
    t = token.encode("utf-8")
    found = None
    for k, v in table.items():
        if hmac.compare_digest(k.encode("utf-8"), t):
            found = v
    return found


def _has(table: Iterable, token: str) -> bool:
    t = token.encode("utf-8")
    ok = False
    for k in table:
        if hmac.compare_digest(k.encode("utf-8"), t):
            ok = True
    return ok


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
                contract.check_response("QuestionAuthoringDeployInterface", "ReadyzResponse", body)
                return (200 if ok else 503), body
            if path == "/v1/question-sets" or path.startswith("/v1/question-sets/"):
                self._reader_auth(headers)          # 읽기 토큰 또는 저작 토큰
                if method != "GET":
                    raise BoundaryError("NOT_FOUND", f"없는 경로: {method} {path}")
                return 200, self._read(path, q)
            actor = self._actor(headers)            # 저작 경로: 읽기 토큰은 여기서 403
            return 200, self._route(method, path, q, headers, body, actor)
        except contract.ResponseContractError as e:   # 서버 결함: 계약을 어긴 응답은 내보내지 않는다. 메시지는 위치와 규칙 이름뿐이다
            print(f"응답 계약 위반: {e}", flush=True)
            return 503, BoundaryError("UNAVAILABLE", "내부 오류").to_record()
        except AuthError as e:
            return e.status, e.to_record()
        except BoundaryError as e:
            return STATUS[e.code], e.to_record()
        except Exception as e:  # 예상 밖의 오류. 연결을 끊지 않고 JSON 503(UNAVAILABLE)으로 답한다. 상태 코드는 CONVENTIONS 9절 표가 고정한다(500은 표에 없다). 원문 메시지는 쓰지 않는다(접속 정보·자료가 섞일 수 있다)
            print(f"내부 오류: {type(e).__name__}", flush=True)
            return 503, BoundaryError("UNAVAILABLE", "내부 오류").to_record()

    def _bearer(self, headers) -> Optional[str]:
        auth = {k.lower(): v for k, v in headers.items()}.get("authorization", "")
        m = re.match(r"^Bearer (\S+)$", auth)
        return m.group(1) if m else None

    def _reader_auth(self, headers) -> None:
        f = self.auth_failure(headers)
        if f is not None:
            raise AuthError(f, "인증이 필요하다" if f == 401 else "권한이 없다")

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
            qsid, what = unquote(m.group(1)), m.group(2)   # 왜: 공식 클라이언트는 조각마다 quote(safe="")로 보낸다(예: ':' -> %3A). 정규식으로 '/'를 나눈 뒤에 디코딩해야 %2F가 경로를 쪼개지 않는다
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
            qsid, ver, sub = unquote(m.group(1)), unquote(m.group(2)), m.group(3)
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

    def _classify(self, headers):
        """(actor, reader_ok, failure). 토큰 표는 모두 끝까지 비교한다(ADR 0013).
        failure: None(받아들임) | 401(토큰 없음·틀림, 또는 gateway 토큰인데 X-Openit-User 없음) | 403(토큰은 맞으나 사용자 모름·형식 불가)."""
        h = {k.lower(): v for k, v in headers.items()}
        m = re.match(r"^Bearer (\S+)$", h.get("authorization", ""))
        if not m:
            return None, False, 401
        tok = m.group(1)
        static = _lookup(self.tokens, tok)          # 정적 표·gateway 표·읽기 표를 모두 비교한다(어느 표에서 맞았는지가 시간에 드러나지 않게)
        is_gw = _has(self.gateway_tokens, tok)
        reader = _lookup(self.read_tokens, tok) is not None
        if static is not None:                      # 정적 토큰: X-Openit-User는 무시한다(클라이언트가 보낸 값을 믿지 않는다)
            return static, True, None
        if is_gw:
            user = h.get("x-openit-user", "")
            if not user:
                return None, False, 401             # 사람 행위자 호출에 신원이 없다(계약: X-Openit-User 없으면 401)
            role = self.user_roles.get(user)
            if role is None or not _USER_RE.match(user):
                return None, False, 403             # 신원은 왔으나 이 서비스가 아는 사용자가 아니다
            # 계약의 Id(reviewer_id)는 '@'를 허용하지 않는다. 이메일형 ID는 '@'를 ':at:'로 바꿔 기록한다(역변환 가능, 충돌 없음: 입력에 ':'를 허용하지 않는다)
            return Actor(user.replace("@", ":at:"), role), True, None
        if reader:
            return None, True, None                 # 읽기 토큰: 읽기 면만. 저작 경로는 403
        return None, False, 401

    def auth_failure(self, headers) -> Optional[int]:
        """읽기 면(I1)과 본문 읽기 전 검사용: 받아들일 자격이면 None, 아니면 401 또는 403."""
        return self._classify(headers)[2]

    def _principal(self, headers) -> Optional[Actor]:
        return self._classify(headers)[0]

    def _actor(self, headers) -> Actor:
        actor, _, f = self._classify(headers)
        if actor is not None:
            return actor
        if f is None:                               # 읽기 토큰으로 저작 경로: 토큰은 맞지만 권한이 없다
            raise AuthError(403, "이 토큰은 읽기 전용이다")
        raise AuthError(f, "인증이 필요하다" if f == 401 else "권한이 없다")

    def _route(self, method, path, q, headers, body, actor):
        s = self.svc
        # 응답은 계약 스키마로 자기 검사를 거쳐 나간다(AuthoringResponses, SignalsResponse). 서버가 키를 늘리면 여기서 먼저 깨진다
        out = lambda d, v: contract.check_response("AuthoringResponses", d, v)   # noqa: E731
        if method == "GET" and path == "/v1/signals":
            s.require_reader(actor)                    # 저자 또는 검수자. 읽기 토큰은 여기 오기 전에 403
            if self.signals is None:
                return contract.check_response("SignalsResponse", None, {
                    "status": "DISABLED", "reason": "I7(ops-feedback)가 설정되지 않았다(QA_FAILURES_URL)"})
            contract.check_request("SignalsQuery", q)
            mx = int(q.get("max_cases", 2000))
            if not 1 <= mx <= 20000:                   # 형식(숫자)은 스키마가, 범위는 여기서(스키마 설명: 1~20000)
                raise BoundaryError("INVALID_RECORD", "max_cases는 1~20000이어야 한다")
            from .signals import build_signals
            try:
                res = build_signals(self.signals, question_set_id=q.get("question_set_id"),
                                    review_status=q.get("review_status"), max_cases=mx,
                                    deadline_seconds=self.signals_deadline)
            except BoundaryError as e:
                if e.code in ("INTEGRITY_ERROR", "INVALID_RECORD", "UNSUPPORTED_VERSION"):   # 상류의 깨진 응답은 성공으로 취급하지 않는다(FAULT_POLICY 10)
                    raise BoundaryError("UNAVAILABLE", "ops-feedback의 응답이 올바르지 않다") from None
                raise
            return contract.check_response("SignalsResponse", None, res)
        if method == "POST" and path == "/v1/drafts":
            b = _body("CreateDraftRequest", body)
            return out("Draft", s.create_draft(actor, b["question_set_id"], base_version=b.get("base_version", "latest")))
        if method == "GET" and path == "/v1/drafts":
            s.require_reader(actor)
            return out("DraftList", {"drafts": s.list_drafts(q.get("question_set_id"))})
        if method == "POST" and path == "/v1/materials":
            contract.check_request("MaterialQuery", q)
            return out("MaterialIngested", s.ingest_material(
                actor, q["question_set_id"], q["filename"], body, base_version=q.get("base_version", "latest"),
                extractor=q.get("extractor"), accept_phi_warning=q.get("accept_phi_warning") == "true"))
        if method == "POST" and path == "/v1/reviews":
            b = _body("AddReviewRequest", body)
            return out("AddReviewResponse", s.add_review(
                actor, b["question_set"], decision=b["decision"], clinical_basis_approved=b["clinical_basis_approved"],
                reason=b.get("reason"), review_id=b.get("review_id")))
        m = re.match(r"^/v1/drafts/([^/]+)(?:/(preview|edits|acknowledge|confirm|discard))?$", path)
        if m:
            did, act = m.groups()
            if method == "GET" and act is None:
                s.require_reader(actor)
                return out("Draft", s.get_draft(did))
            if method == "GET" and act == "preview":
                s.require_reader(actor)
                return out("Preview", s.get_preview(did))
            if method == "POST" and act == "edits":
                return out("Draft", s.apply_edits(actor, did, _body("EditsRequest", body)["edits"]))
            if method == "POST" and act == "acknowledge":
                return out("Draft", s.acknowledge_flags(actor, did, _body("AcknowledgeRequest", body)["flag_ids"]))
            if method == "POST" and act == "confirm":
                return out("ConfirmResponse", s.confirm(actor, did, _body("ConfirmRequest", body)["preview_hash"]))
            if method == "POST" and act == "discard":
                return out("Draft", s.discard_draft(actor, did, _body("DiscardRequest", body)["reason"]))
        raise BoundaryError("NOT_FOUND", f"없는 경로: {method} {path}")


# 왜 8MiB: 도입 시 근거 기록 없음. 문항 자료는 작은 텍스트 양식이라 넉넉한 선이다. waitress의 max_request_body_size로 강제한다(초과는 헤더 단계에서 413). ADR 0016
MAX_BODY = 8 * 1024 * 1024


def wsgi_app(api: Api):
    """Api.handle()을 WSGI(PEP 3333) 앱으로 감싼다. 서버(waitress)가 소켓·스레드·timeout·본문 상한을 맡는다. ADR 0016"""
    def app(environ, start_response):
        headers = {k[5:].replace("_", "-").title(): v for k, v in environ.items() if k.startswith("HTTP_")}
        n = int(environ.get("CONTENT_LENGTH") or 0)      # waitress가 Content-Length 형식·상한을 이미 검증했다(R7)
        body = environ["wsgi.input"].read(n) if n else b""
        status, out = api.handle(environ["REQUEST_METHOD"], environ["REQUEST_URI"], headers, body)
        data = json.dumps(out, ensure_ascii=False).encode("utf-8")
        hdrs = [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(data)))]
        if status == 401:
            hdrs.append(("WWW-Authenticate", "Bearer"))   # RFC 9110 11.6.1(401은 도전 필수), RFC 6750 3절(Bearer)
        start_response(f"{status} {HTTPStatus(status).phrase}", hdrs)
        return [data]
    return app


def make_server(api: Api, host: str, port: int, request_timeout: float = 30.0, max_concurrent: int = 32):
    """waitress 서버(단일 프로세스, 다중 스레드). run()으로 돌리고 close()로 닫는다. 값의 출처는 ADR 0016.
    threads=max_concurrent: 동시에 처리하는 요청 수. 넘는 요청은 줄 서서 기다린다(이전의 503 거절을 대체).
    channel_timeout=request_timeout: 이 시간 동안 아무 입출력이 없는 연결(멈춘 업로드)을 끊는다.
    cleanup_interval=1: 기본 30초면 channel_timeout이 최대 30초 늦게 적용된다. 정수 초의 최솟값."""
    return create_server(
        wsgi_app(api), host=host, port=port, threads=max_concurrent, channel_timeout=max(1, int(round(request_timeout))),
        cleanup_interval=1, max_request_body_size=MAX_BODY, clear_untrusted_proxy_headers=True, map={})


def drain(srv, grace: float) -> int:
    """종료 신호 처리: 새 연결을 받지 않고, 처리 중인 요청과 그 응답 전송을 유예 시간까지 기다린다. 못 끝낸 요청 수를 돌려준다.
    왜 직접: waitress는 우아한 종료(진행 중 요청 마치기)를 하지 않는다(close가 바로 스레드를 멈춘다)."""
    srv.accepting = False
    dispatcher = srv.task_dispatcher
    end = time.monotonic() + grace
    while time.monotonic() < end and (dispatcher.active_count or dispatcher.queue):
        time.sleep(0.05)
    left = dispatcher.active_count + len(dispatcher.queue)
    while time.monotonic() < end and any(getattr(c, "total_outbufs_len", 0) for c in list(srv._map.values())):
        time.sleep(0.05)
    return left
