"""통신(받는 쪽): 저작 API의 HTTP 면. 계약 HTTP 규약(401/403, X-Openit-User, 413, BoundaryError 본문, /healthz, /readyz)은 openit_link.App이 지킨다(D118).
이 파일이 하는 일: 경로 표(Route)를 적고, 토큰이 가리키는 호출자(Actor)를 정하고, 계약 요청/응답 모양과 영역 내부 호출(service) 사이를 옮긴다.
handle()은 소켓 없이 시험할 수 있는 입구이고, app이 WSGI 앱, make_server()가 waitress로 감싼다. 토큰 표는 서비스 진입점이 환경 변수에서 만들어 넘긴다.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
from typing import Callable, Iterable, Mapping, Optional
from urllib.parse import unquote, urlparse

from boundary_contracts.errors import BoundaryError
from openit_link import App, Response, Route
from openit_link.server import STATUS
from waitress import create_server

import boundary_contracts

_cache: dict = {}


@dataclass(frozen=True)
class Actor:
    """호출자 신원(토큰이나 gateway 사용자 표에서 정해진다). 이 영역 안의 업무 컴포넌트는 이것을 {id, role} 사전으로만 받는다."""
    id: str
    role: str


class ResponseContractError(Exception):
    """서버가 계약을 어기는 응답을 만들었다(서버 결함). 호출자에게는 UNAVAILABLE로만 나간다."""


def _problems(schema: str, def_name: Optional[str], value) -> list[str]:
    key = (schema, def_name)
    if key not in _cache:
        v = boundary_contracts.validator(schema)
        _cache[key] = v.evolve(schema={"$ref": f"#/$defs/{def_name}"}) if def_name else v
    return ["/" + "/".join(map(str, e.absolute_path)) + f": {e.validator} 규칙 위반"
            for e in sorted(_cache[key].iter_errors(value), key=lambda e: list(map(str, e.absolute_path)))]


INPUT_SCHEMA = "AuthoringRequests"     # 이 면은 계약 스키마가 입출력이다(요청 AuthoringRequests, 응답 AuthoringResponses·SignalsResponse)
OUTPUT_SCHEMA = "AuthoringResponses"


class contract:   # noqa: N801  이 파일이 쓰는 계약 검사 묶음(요청 AuthoringRequests, 응답 AuthoringResponses·SignalsResponse·QuestionAuthoringDeployInterface)
    ResponseContractError = ResponseContractError

    @staticmethod
    def check_request(def_name: str, value) -> None:
        """요청 본문·쿼리를 AuthoringRequests의 $defs 하나로 검사한다. 모르는 키는 거부한다(MANIFEST note)."""
        errs = _problems("AuthoringRequests", def_name, value)
        if errs:
            raise BoundaryError("INVALID_RECORD", f"AuthoringRequests.{def_name} 위반: {errs[0]}", {"errors": errs[:20]})

    @staticmethod
    def check_response(schema: str, def_name: Optional[str], value):
        errs = _problems(schema, def_name, value)
        if errs:
            raise ResponseContractError(f"{schema}{'.' + def_name if def_name else ''} 위반: {errs[0]}")
        return value

STATUS = {"NOT_FOUND": 404, "INVALID_RECORD": 400, "UNSUPPORTED_VERSION": 422, "CONFLICT": 409,
          "INTEGRITY_ERROR": 422, "FAILED_PRECONDITION": 412, "PERMISSION_DENIED": 403, "UNAVAILABLE": 503}


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
_SEP = "\x1f"   # 토큰이 가리키는 호출자를 라이브러리의 '역할' 문자열에 싣는 구분자(ID·역할 문자에 없다)

# 왜 8MiB: 도입 시 근거 기록 없음. 문항 자료는 작은 텍스트 양식이라 넉넉한 선이다. 라이브러리 Route.max_body와 waitress max_request_body_size가 강제한다(초과는 413). ADR 0016
MAX_BODY = 8 * 1024 * 1024


class Api:
    def __init__(self, service, tokens: Mapping[str, Actor], *,
                 read_tokens: Optional[Mapping[str, str]] = None, signals=None, ready: Callable[[], bool] = lambda: True,
                 db_kind: Optional[str] = None, signals_deadline: Optional[float] = 20.0,
                 gateway_tokens: Optional[Iterable[str]] = None, user_roles: Optional[Mapping[str, str]] = None,
                 warnings: Optional[list] = None):
        self.signals_deadline = signals_deadline
        # gateway 경유 인증(D88 E2): gateway가 사람 인증을 마치고 서비스 토큰(gateway_tokens)과 X-Openit-User를 붙인다.
        # 사람의 역할은 이 서비스의 user_roles(사용자 ID -> 역할) 표가 정한다. 헤더를 믿는 것은 gateway 토큰을 가진 호출뿐이다.
        self.user_roles = dict(user_roles or {})
        self.warnings = list(warnings or [])
        self.svc = service
        self.signals = signals                         # FailureSource | None (I7 미설정이면 None)
        self.ready = ready
        self.db_kind = db_kind                         # 'sqlite' | 'postgres' (종류만. 접속 정보는 절대 쓰지 않는다)
        # 토큰 -> 역할 문자열. 같은 토큰이 여러 표에 있으면 정적 > gateway > 읽기 순으로 이긴다
        tm = {t: "reader" for t in (read_tokens or {})}
        tm.update({t: "gateway" for t in (gateway_tokens or ())})
        actor_roles = set()
        for t, a in dict(tokens).items():
            code = "actor" + _SEP + a.id + _SEP + a.role
            tm[t] = code
            actor_roles.add(code)
        write = frozenset(actor_roles | ({"gateway"} if gateway_tokens else set()))
        read = frozenset(write | {"reader"})
        self.app = App(self._routes(read, write), tm, ready=self._readyz)

    # ---- 입구 ----
    def handle(self, method: str, url: str, headers: Mapping[str, str], body: bytes = b"") -> tuple[int, dict]:
        """소켓 없이 라우팅 전체(인증 포함)를 거친다. (상태, 본문 사전)."""
        import io
        u = urlparse(url)
        env = {"REQUEST_METHOD": method, "PATH_INFO": unquote(u.path), "QUERY_STRING": u.query,
               "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
        for k, v in headers.items():
            env["HTTP_" + k.upper().replace("-", "_")] = v
        status, hdrs, data = self.app.handle(env)
        return status, (json.loads(data) if data else {})

    def _readyz(self):
        ok = bool(self.ready())
        body = {"status": "ready" if ok else "not-ready"}
        if self.db_kind:
            body["db"] = self.db_kind
        if self.warnings:
            body["warnings"] = list(self.warnings)     # 200을 막지 않는다(경고만)
        try:
            contract.check_response("QuestionAuthoringDeployInterface", "ReadyzResponse", body)
        except ResponseContractError as e:   # 서버 결함: 계약을 어긴 본문은 내보내지 않는다
            print(f"응답 계약 위반: {e}", flush=True)
            return False, {"status": "not-ready"}
        return ok, body

    def _actor(self, req) -> Optional[Actor]:
        role = req.role
        if role == "reader":
            return None
        if role == "gateway":
            user = req.user
            if not user:       # 사람 행위자 호출에 신원이 없다(계약: X-Openit-User 없으면 401)
                raise BoundaryError("PERMISSION_DENIED", "인증이 필요하다", {"http_status": 401})
            r = self.user_roles.get(user)
            if r is None or not _USER_RE.match(user):
                raise BoundaryError("PERMISSION_DENIED", "권한이 없다")      # 신원은 왔으나 이 서비스가 아는 사용자가 아니다
            # 계약의 Id(reviewer_id)는 '@'를 허용하지 않는다. 이메일형 ID는 '@'를 ':at:'로 바꿔 기록한다(역변환 가능, 충돌 없음: 입력에 ':'를 허용하지 않는다)
            return Actor(user.replace("@", ":at:"), r)
        _, aid, arole = role.split(_SEP)
        return Actor(aid, arole)

    def _wrap(self, fn):
        def handler(req):
            try:
                return Response(200, fn(req, self._actor(req)))
            except BoundaryError:
                raise
            except contract.ResponseContractError as e:   # 서버 결함: 계약을 어긴 응답은 내보내지 않는다. 메시지는 위치와 규칙 이름뿐이다
                print(f"응답 계약 위반: {e}", flush=True)
                raise BoundaryError("UNAVAILABLE", "내부 오류") from None
            except Exception as e:  # 예상 밖의 오류. 연결을 끊지 않고 JSON 503(UNAVAILABLE)으로 답한다. 원문 메시지는 쓰지 않는다(접속 정보·자료가 섞일 수 있다)
                print(f"내부 오류: {type(e).__name__}", flush=True)
                raise BoundaryError("UNAVAILABLE", "내부 오류") from None
        return handler

    def _routes(self, read, write):
        W = lambda m, p, fn, roles=write: Route(m, p, self._wrap(fn), roles=roles, raw_body=True, max_body=MAX_BODY)   # noqa: E731
        sc = "/v1/question-sets"
        return [
            W("GET", sc, lambda r, a: self._read(r), read),
            W("GET", sc + "/{qsid}/latest", lambda r, a: self._read(r), read),
            W("GET", sc + "/{qsid}/versions", lambda r, a: self._read(r), read),
            W("GET", sc + "/{qsid}/versions/{ver}", lambda r, a: self._read(r), read),
            W("GET", sc + "/{qsid}/versions/{ver}/reviews", lambda r, a: self._read(r), read),
            W("GET", sc + "/{qsid}/versions/{ver}/change", lambda r, a: self._read(r), read),
            W("GET", "/v1/signals", self._signals),
            W("POST", "/v1/drafts", self._create),
            W("GET", "/v1/drafts", self._list),
            W("POST", "/v1/materials", self._material),
            W("POST", "/v1/reviews", self._review),
            W("GET", "/v1/drafts/{did}", lambda r, a: self._out("Draft", self._need_reader(a) and self.svc.get_draft(r.params["did"]))),
            W("GET", "/v1/drafts/{did}/preview", lambda r, a: self._out("Preview", self._need_reader(a) and self.svc.get_preview(r.params["did"]))),
            W("POST", "/v1/drafts/{did}/edits", lambda r, a: self._out("Draft", self.svc.apply_edits(a, r.params["did"], _body("EditsRequest", r.raw)["edits"]))),
            W("POST", "/v1/drafts/{did}/acknowledge", lambda r, a: self._out("Draft", self.svc.acknowledge_flags(a, r.params["did"], _body("AcknowledgeRequest", r.raw)["flag_ids"]))),
            W("POST", "/v1/drafts/{did}/confirm", lambda r, a: self._out("ConfirmResponse", self.svc.confirm(a, r.params["did"], _body("ConfirmRequest", r.raw)["preview_hash"]))),
            W("POST", "/v1/drafts/{did}/discard", lambda r, a: self._out("Draft", self.svc.discard_draft(a, r.params["did"], _body("DiscardRequest", r.raw)["reason"]))),
        ]

    # 응답은 계약 스키마로 자기 검사를 거쳐 나간다(AuthoringResponses, SignalsResponse). 서버가 키를 늘리면 여기서 먼저 깨진다.
    # 왜 라이브러리의 response_schema가 아닌가: 라이브러리는 계약 파일 전체만 가리키고 $defs 하나(예: AuthoringResponses.Draft)를 못 가리킨다(docs/change-requests/CR-0008).
    @staticmethod
    def _out(name, value):
        return contract.check_response("AuthoringResponses", name, value)

    def _need_reader(self, actor) -> bool:
        self.svc.require_reader(actor)      # 저자 또는 검수자. 읽기 토큰은 라이브러리가 이 경로 앞에서 403
        return True

    def _create(self, req, actor):
        b = _body("CreateDraftRequest", req.raw)
        return self._out("Draft", self.svc.create_draft(actor, b["question_set_id"], base_version=b.get("base_version", "latest")))

    def _list(self, req, actor):
        self.svc.require_reader(actor)
        return self._out("DraftList", {"drafts": self.svc.list_drafts(req.query.get("question_set_id"))})

    def _material(self, req, actor):
        q = req.query
        contract.check_request("MaterialQuery", q)
        return self._out("MaterialIngested", self.svc.ingest_material(
            actor, q["question_set_id"], q["filename"], req.raw, base_version=q.get("base_version", "latest"),
            extractor=q.get("extractor"), accept_phi_warning=q.get("accept_phi_warning") == "true"))

    def _review(self, req, actor):
        b = _body("AddReviewRequest", req.raw)
        return self._out("AddReviewResponse", self.svc.add_review(
            actor, b["question_set"], decision=b["decision"], clinical_basis_approved=b["clinical_basis_approved"],
            reason=b.get("reason"), review_id=b.get("review_id")))

    def _signals(self, req, actor):
        q = req.query
        self.svc.require_reader(actor)                 # 저자 또는 검수자. 읽기 토큰은 여기 오기 전에 403
        if self.signals is None:
            return contract.check_response("SignalsResponse", None, {
                "status": "DISABLED", "reason": "I7(ops-feedback)가 설정되지 않았다(QA_FAILURES_URL)"})
        contract.check_request("SignalsQuery", q)
        mx = int(q.get("max_cases", 2000))
        if not 1 <= mx <= 20000:                       # 형식(숫자)은 스키마가, 범위는 여기서(스키마 설명: 1~20000)
            raise BoundaryError("INVALID_RECORD", "max_cases는 1~20000이어야 한다")
        try:
            res = self.svc.build_signals(self.signals, question_set_id=q.get("question_set_id"),
                                         review_status=q.get("review_status"), max_cases=mx,
                                         deadline_seconds=self.signals_deadline)
        except BoundaryError as e:
            if e.code in ("INTEGRITY_ERROR", "INVALID_RECORD", "UNSUPPORTED_VERSION"):   # 상류의 깨진 응답은 성공으로 취급하지 않는다(FAULT_POLICY 10)
                raise BoundaryError("UNAVAILABLE", "ops-feedback의 응답이 올바르지 않다") from None
            raise
        return contract.check_response("SignalsResponse", None, res)

    def _page(self, q) -> tuple[Optional[str], int]:
        try:
            limit = int(q.get("limit", 100))
        except ValueError:
            raise BoundaryError("INVALID_RECORD", "limit은 정수여야 한다")
        if not 1 <= limit <= 500:
            raise BoundaryError("INVALID_RECORD", "limit은 1~500이어야 한다")
        return q.get("cursor"), limit

    def _read(self, req):
        """I1 QuestionReader의 HTTP 읽기 면(boundary-contracts/HTTP.md). 응답은 스키마 검증을 거친다."""
        r = self.svc.store.reader()
        v = self.svc
        p, q, path = req.params, req.query, req.path
        qsid = p.get("qsid")
        if qsid is None:
            cur, lim = self._page(q)
            items, nxt = r.list_ids(cursor=cur, limit=lim)
            return {"items": items, "next_cursor": nxt}
        ver = p.get("ver")
        if ver is None and path.endswith("/latest"):
            flag = q.get("reviewed_only", "false")
            if flag not in ("true", "false"):
                raise BoundaryError("INVALID_RECORD", "reviewed_only는 true 또는 false")
            return r.latest(qsid, reviewed_only=flag == "true")
        if ver is None:
            cur, lim = self._page(q)
            items, nxt = r.list_versions(qsid, cursor=cur, limit=lim)
            return {"items": items, "next_cursor": nxt}
        ref = self.svc.store.ref_of(qsid, ver)
        if path.endswith("/reviews"):
            revs = r.get_reviews(ref)
            for x in revs:
                v.check("ReviewRecord", x)
            return {"items": revs, "next_cursor": None}
        if path.endswith("/change"):
            ch = r.get_change(ref)
            if ch is not None:
                v.check("ChangeClassification", ch)
            return {"item": ch}
        bank = r.get_question_bank(ref)      # 해시 재계산 포함
        v.check("QuestionBank", bank)
        return bank


def make_server(api: Api, host: str, port: int, request_timeout: float = 30.0, max_concurrent: int = 32):
    """waitress 서버(단일 프로세스, 다중 스레드). run()으로 돌리고 close()로 닫는다. 값의 출처는 ADR 0016.
    threads=max_concurrent: 동시에 처리하는 요청 수. 넘는 요청은 줄 서서 기다린다(이전의 503 거절을 대체).
    channel_timeout=request_timeout: 이 시간 동안 아무 입출력이 없는 연결(멈춘 업로드)을 끊는다.
    cleanup_interval=1: 기본 30초면 channel_timeout이 최대 30초 늦게 적용된다. 정수 초의 최솟값."""
    return create_server(
        api.app, host=host, port=port, threads=max_concurrent, channel_timeout=max(1, int(round(request_timeout))),
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


def close_all(srv) -> None:
    """서버 루프 스레드 안에서 부른다(트리거 thunk): 듣는 소켓과 모든 연결을 닫아 이벤트 맵을 비운다. 그러면 srv.run()이 돌아온다."""
    srv.asyncore.close_all(srv._map)
