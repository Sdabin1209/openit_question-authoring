"""서비스 진입점. 환경 변수를 읽는 곳은 여기뿐이다.

QA_DATABASE_URL       PostgreSQL 접속 문자열(운영). 비밀 값: QA_DATABASE_URL_FILE 우선. 있으면 PostgreSQL을 쓴다
QA_DB_PATH            QA_DATABASE_URL이 없을 때 쓰는 SQLite 파일 경로(개발·시험, 기본 /data/questions.sqlite3)
비밀(QA_AUTHOR_TOKENS, QA_READ_TOKENS, QA_FAILURES_TOKEN)은 `boundary_contracts.read_secret`으로 읽는다:
NAME_FILE(파일 경로)이 우선이고, 없을 때만 NAME 환경 변수(개발용)를 쓴다. 운영에서는 NAME_FILE을 쓴다.
QA_AUTHOR_TOKENS      '토큰:actor-id:역할' 쉼표 목록. 비밀 값이므로 이미지에 넣지 않는다
QA_READ_TOKENS        '역할=토큰' 쉼표 목록. I1 읽기 전용 토큰(저작 경로는 403). 비밀 값
QA_FAILURES_URL      ops-feedback(I7) 기본 URL. 비우면 개선 신호는 DISABLED로 응답
QA_FAILURES_TOKEN_ENV I7 읽기 토큰의 비밀 이름(기본 QA_FAILURES_TOKEN; QA_FAILURES_TOKEN_FILE로도 준다). 값은 이미지에 넣지 않는다
QA_HOST, QA_PORT      기본 0.0.0.0, 8080
QA_DB_CONNECT_TIMEOUT_SECONDS    PostgreSQL 접속 상한(초, 기본 3)
QA_DB_STATEMENT_TIMEOUT_SECONDS  PostgreSQL 문장 하나의 상한(초, 기본 3). 호출 하나는 이 값 + 1초 안에 끝난다
QA_READYZ_DB_TIMEOUT_SECONDS     /readyz의 DB 확인 상한(초, 기본 2)
QA_DB_LARGE_STATEMENT_TIMEOUT_SECONDS  큰 자료(원본 자료, 초안, 문항 세트 버전) 쓰기의 문장 상한(초, 기본 30)
QA_SHUTDOWN_GRACE_SECONDS       SIGTERM 뒤 진행 중 요청을 기다리는 최대 시간(기본 8초)
QA_REQUEST_TIMEOUT_SECONDS       HTTP 요청 소켓의 무응답 상한(초, 기본 30). 업로드가 이 시간 동안 멈추면 끊는다
QA_DEV_MODE                      1이면 개발·시험 구성을 허용한다(SQLite, 토큰 없이 기동). 없으면 운영 구성이어야 시작한다:
                                 QA_DATABASE_URL(_FILE)과 토큰(QA_AUTHOR_TOKENS 또는 QA_GATEWAY_TOKENS)이 없으면 시작을 거부한다
QA_REQUIRE_TWO_PERSON            기본 false(L13, ADR 0026). true면 저자 != 검수자(4-eyes, D92)이고 임상 승인은 검수 역할이 한다. false면 /readyz에 경고(two_person_disabled)
QA_GATEWAY_TOKENS                비밀. gateway가 쓰는 서비스 토큰 쉼표 목록. 이 토큰의 요청은 X-Openit-User 헤더의 사용자를 쓴다
QA_MAX_CONCURRENT_REQUESTS       동시 처리 요청 상한(기본 32). 넘는 요청은 줄 서서 기다린다(waitress 스레드 수, ADR 0016)
QA_DRAFT_RETENTION_DAYS          처리되지 않은(OPEN, DISCARDED) 초안의 보존 일수(기본 30, 양의 정수만). `python -m question_authoring purge`가 쓴다
QA_SOURCE_RETENTION_DAYS         어떤 버전·초안도 참조하지 않는 원본 자료의 보존 일수(기본 30, 양의 정수만)
QA_GATE_URL, QA_GATE_TOKEN(_ENV·_FILE), QA_GATE_TIMEOUT_SECONDS  release-gate(I4) 판정 조회(저작 화면). URL 을 비우면 판정 조회는 꺼진다(412). 시간 기본 10초(R10.3)
QA_FAILURES_TIMEOUT_SECONDS      I7(ops-feedback) 호출 하나의 읽기 상한(초, 기본 10, RUNTIME R10.3: 사슬 표에 없는 서비스 사이 호출의 시작값, 측정 안 함)
QA_SIGNALS_DEADLINE_SECONDS      GET /v1/signals 한 번의 총 마감(초, 기본 110)
QA_REQUEST_DEADLINE_SECONDS      사람용 요청 하나의 서버 쪽 전체 마감(초, 기본 110.0 = 측정 최악 62초 위의 여유, gateway 응답 머리글 120초 안, D125, ADR 0025)
"""
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

from boundary_contracts import read_secret
from boundary_contracts.clients import egress_proxy_from
from boundary_contracts.errors import BoundaryError

from .components.link_authoring_server import Api, Actor, close_all, drain, make_server
from .components.link_failures_read import http_failure_source
from .components.link_gate_read import http_gate_source
from .components.question_store import QuestionStore as AuthoringStore
from .wiring import contract
from .wiring.deadline import DEFAULT_REQUEST_DEADLINE_SECONDS, RequestDeadline
from .wiring.service import AuthoringService
from .wiring.stores import PostgresQuestionStore, SqliteQuestionStore


def check_environment(environ=None) -> None:
    """QA_ 환경 변수를 계약 QuestionAuthoringEnv(3.2.0)로 검사한다(형식: 숫자, 열거, 역할 목록).
    왜 여기서 한 번: 변수마다 손으로 파싱하면 스키마와 어긋나도 깨지지 않는다(예: 보존 일수 0이 build()에서는 통과). 값은 오류에 쓰지 않는다(비밀이 섞일 수 있다).
    빈 문자열은 "설정 안 함"으로 본다(compose가 비어 있는 변수를 넘기는 경우). 범위(0 < 초 <= 600)는 스키마 설명대로 _seconds가 검사한다."""
    environ = os.environ if environ is None else environ
    env = {k: v for k, v in environ.items() if k.startswith("QA_") and v.strip() != ""}
    errs = contract.environment_problems(env)
    if errs:
        raise SystemExit("환경 변수가 계약(QuestionAuthoringEnv)을 어긴다: " + "; ".join(errs[:5]))
    # 모든 영역 컨테이너 공통 이름(OPENIT_*)은 CommonEnv 가 정한다(3.4.4 검토 A-1). 값은 오류에 쓰지 않는다
    common = {k: v for k, v in environ.items() if k.startswith("OPENIT_") and v.strip() != ""}
    errs = contract.problems("CommonEnv", None, common)
    if errs:
        raise SystemExit("환경 변수가 계약(CommonEnv)을 어긴다: " + "; ".join(errs[:5]))


def check_backup_environment(environ=None) -> None:
    """백업·복구 잡의 환경 변수를 계약 QuestionAuthoringBackupEnv(4.0.x, allOf BackupJobEnv -> CommonEnv)로 검사한다.
    왜 서비스 Env(QuestionAuthoringEnv)가 아닌가: 그 스키마는 서비스 기동 조건(운영이면 저자·gateway 토큰 필수)을 갖는다. 백업 잡에는 토큰이 주입되지 않으므로
    서비스 Env 로 검사하면 운영 구성에서 잡이 언제나 거부된다(2026-11-08 E2E 단계 9). 계약은 잡 전용 스키마를 따로 둔다(이름·형식은 같은 정의를 $ref).
    QA_·OPENIT_ 이름만 넘긴다(다른 접두사는 계약 밖). 빈 문자열은 "설정 안 함". 값은 오류에 쓰지 않는다(비밀이 섞일 수 있다)."""
    environ = os.environ if environ is None else environ
    env = {k: v for k, v in environ.items() if k.startswith(("QA_", "OPENIT_")) and v.strip() != ""}
    errs = contract.problems("QuestionAuthoringBackupEnv", None, env)
    if errs:
        raise SystemExit("환경 변수가 계약(QuestionAuthoringBackupEnv)을 어긴다: " + "; ".join(errs[:5]))


def _seconds(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    v = float(raw)           # 형식은 check_environment가 보증했다
    if not 0 < v <= 600:
        raise SystemExit(f"{name}은 0보다 크고 600 이하여야 한다")
    return v


def open_store(remaining=lambda: None) -> AuthoringStore:
    url = read_secret("QA_DATABASE_URL")
    if url:
        return PostgresQuestionStore(
            url, connect_timeout=_seconds("QA_DB_CONNECT_TIMEOUT_SECONDS", 3.0),
            statement_timeout=_seconds("QA_DB_STATEMENT_TIMEOUT_SECONDS", 3.0),
            probe_timeout=_seconds("QA_READYZ_DB_TIMEOUT_SECONDS", 2.0),
            large_statement_timeout=_seconds("QA_DB_LARGE_STATEMENT_TIMEOUT_SECONDS", 30.0), remaining=remaining)
    return SqliteQuestionStore(os.environ.get("QA_DB_PATH", "/data/questions.sqlite3"))


ROLES = ("question-author", "clinical-reviewer")


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw in ("1", "true")   # 열거(1/0/true/false)는 check_environment가 보증했다


def _days(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)           # 양의 정수 형식은 check_environment가 보증했다. 보존 기간 없음(무기한)은 스키마가 거부한다


def _require_production_config(*, need_tokens: bool) -> None:
    """운영 구성 검사(전수 점검 P6). QA_DEV_MODE=1이 아니면 DB URL(과 서비스 기동이면 토큰)이 있어야 한다.
    purge도 같은 검사를 거친다: 개발 플래그 없이 빈 임시 SQLite를 열어 "0건 삭제"로 성공하는 일이 없게 한다."""
    if _bool("QA_DEV_MODE", False):
        return
    if not read_secret("QA_DATABASE_URL"):
        raise SystemExit("QA_DATABASE_URL(_FILE)이 없다. 개발·시험이면 QA_DEV_MODE=1을 준다")
    if need_tokens and not (read_secret("QA_AUTHOR_TOKENS") or read_secret("QA_GATEWAY_TOKENS")):
        raise SystemExit("저자 토큰이 없다(QA_AUTHOR_TOKENS 또는 QA_GATEWAY_TOKENS). 개발·시험이면 QA_DEV_MODE=1을 준다")


def build() -> tuple[Api, AuthoringStore]:
    env = os.environ
    check_environment()
    dev = _bool("QA_DEV_MODE", False)
    _require_production_config(need_tokens=True)
    tokens = {}
    for item in filter(None, (read_secret("QA_AUTHOR_TOKENS") or "").split(",")):
        tok, aid, role = item.split(":")
        if role not in ROLES:
            raise SystemExit(f"QA_AUTHOR_TOKENS의 역할은 {', '.join(ROLES)} 중 하나여야 한다")
        tokens[tok] = Actor(aid, role)
    gateway_tokens = {t for t in (read_secret("QA_GATEWAY_TOKENS") or "").split(",") if t}
    deadline = RequestDeadline(time.monotonic, _seconds("QA_REQUEST_DEADLINE_SECONDS", DEFAULT_REQUEST_DEADLINE_SECONDS))   # 사람용 요청 전체 마감(D120)
    store = open_store(deadline.remaining)
    read_tokens = {}
    for item in filter(None, (read_secret("QA_READ_TOKENS") or "").split(",")):
        role, tok = item.split("=", 1)
        read_tokens[tok] = role
    two = _bool("QA_REQUIRE_TWO_PERSON", False)   # 기본 false(L13, ADR 0026)
    svc = AuthoringService(store, author_roles={"question-author"}, require_two_person=two, deadline=deadline)
    warnings = ([] if two else ["two_person_disabled"])
    signals = None
    if env.get("QA_FAILURES_URL"):
        try:
            signals = http_failure_source(env["QA_FAILURES_URL"], read_secret(env.get("QA_FAILURES_TOKEN_ENV", "QA_FAILURES_TOKEN")) or "",
                                          _seconds("QA_FAILURES_TIMEOUT_SECONDS", 10.0),
                                          egress_proxy=egress_proxy_from(env))   # 이웃 HTTP 클라이언트는 통신 컴포넌트가 만든다. 영역 밖 호출은 자기 영역 게이트웨이 출구로만(R1.8)
        except ValueError as e:     # R1.1: loopback 밖 평문 주소, R1.8: 출구 형식 위반은 기동 때 거부한다(값은 쓰지 않는다)
            raise SystemExit(f"QA_FAILURES_URL 또는 OPENIT_EGRESS_PROXY 가 계약(R1.1·R1.8)을 어긴다: {type(e).__name__}")
    gate = None
    if env.get("QA_GATE_URL"):
        try:
            gate = http_gate_source(env["QA_GATE_URL"], read_secret(env.get("QA_GATE_TOKEN_ENV", "QA_GATE_TOKEN")) or "",
                                    _seconds("QA_GATE_TIMEOUT_SECONDS", 10.0), egress_proxy=egress_proxy_from(env))   # R10.3 기본 10초, 출구는 자기 영역 게이트웨이(R1.8)
        except ValueError:
            raise SystemExit("QA_GATE_URL 또는 OPENIT_EGRESS_PROXY 가 계약(R1.1·R1.8)을 어긴다")
    return Api(svc, tokens, read_tokens=read_tokens, signals=signals, gate=gate, ready=store.is_ready, db_kind=getattr(store, "kind", None),
               signals_deadline=_seconds("QA_SIGNALS_DEADLINE_SECONDS", 110.0), gateway_tokens=gateway_tokens,  # 왜 110.0초: D125 저자 경로 서버 마감과 같은 값, 게이트웨이 response_header 120초 안(서버 110 < 게이트웨이 120 < 화면 130). 측정한 값이 아니라 측정 최악 62초 위의 여유(ADR 0025)
               warnings=warnings, deadline=deadline), store


def purge_once(store: AuthoringStore, now: datetime | None = None) -> dict:
    """보존 기간이 지난 것을 지운다(멱등, 여러 번 돌려도 안전). 스케줄은 deploy가 정한다(D39)."""
    now = now or datetime.now(timezone.utc)
    ts = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")   # noqa: E731
    return store.purge(now=ts(now), draft_before=ts(now - timedelta(days=_days("QA_DRAFT_RETENTION_DAYS", 30))),
                       source_before=ts(now - timedelta(days=_days("QA_SOURCE_RETENTION_DAYS", 30))))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["purge"]:
        check_environment()
        _require_production_config(need_tokens=False)   # 정리 잡에는 저자 토큰이 필요 없어 DB URL과 개발 플래그만 본다
        store = open_store()
        result = purge_once(store)
        store.close()
        # 왜 내보내기 전에 검사: 받는 쪽(deploy)은 다시 검증하지 않는다(R7). 계약 QuestionAuthoringDeployInterface의 $defs.PurgeResult(최상위 스키마 이름이 아니다)
        errs = contract.problems("QuestionAuthoringDeployInterface", "PurgeResult", result)
        if errs:
            raise SystemExit("purge 출력이 계약 PurgeResult를 어긴다: " + "; ".join(errs[:3]))
        print(json.dumps(result), flush=True)   # 왜 JSON: deploy가 한 줄을 읽는다(파이썬 dict 표기는 JSON이 아니다)
        return 0
    if argv[:1] in (["backup"], ["restore"]):
        from .wiring import backup_flow
        ts = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")   # noqa: E731
        started, result, err = ts(), None, None
        try:
            check_backup_environment()
            _require_production_config(need_tokens=False)
        except SystemExit as e:
            # 설정 오류도 마지막 줄 BackupJobResult(failed, INVALID_RECORD) + 종료 코드 1(R9.2, 계약 4.0.0): 판정하는 쪽은 마지막 줄만 읽는다.
            # 메시지는 변수 이름·규칙 이름뿐이다(값 없음)
            err = BoundaryError("INVALID_RECORD", str(e.code))
        try:
            if err is not None:
                raise err
            proxy = egress_proxy_from(os.environ)
            if argv[0] == "backup":
                result = backup_flow.run(os.environ, proxy=proxy)
            else:
                snap = argv[argv.index("--snapshot") + 1] if "--snapshot" in argv[1:] and argv.index("--snapshot") + 1 < len(argv) else "latest"
                result = backup_flow.restore(os.environ, snap, proxy=proxy)
        except (BoundaryError, ValueError) as e:     # 실패도 마지막 줄 JSON 과 0 이 아닌 종료 코드(R9.2, BackupJobResult). 서비스 출력은 바뀌지 않는다. 메시지에는 비밀이 없다
            err = e if isinstance(e, BoundaryError) else BoundaryError("INVALID_RECORD", "OPENIT_EGRESS_PROXY 가 계약(R1.8)을 어긴다")
        line = backup_flow.result_line(started, ts(), ok=result, error=err)
        errs = contract.problems("BackupJobResult", None, line)
        if errs:
            raise SystemExit("출력이 계약 BackupJobResult를 어긴다: " + "; ".join(errs[:3]))      # 생산자 출력 검증(CONVENTIONS 3절)
        print(json.dumps(line), flush=True)
        return backup_flow.exit_code(err)
    api, _ = build()
    srv = make_server(api, os.environ.get("QA_HOST", "0.0.0.0"), int(os.environ.get("QA_PORT") or 8080),
                      request_timeout=_seconds("QA_REQUEST_TIMEOUT_SECONDS", 30.0),
                      max_concurrent=int(os.environ.get("QA_MAX_CONCURRENT_REQUESTS") or 32))
    # 왜 기본 8초: docker stop의 기본 대기(10초) 뒤 SIGKILL보다 짧게 끝내려는 값이다(근거: Docker 문서, 측정 안 함)
    grace = _seconds("QA_SHUTDOWN_GRACE_SECONDS", 8.0)

    def finish():
        left = drain(srv, grace, clock=time.monotonic, sleep=time.sleep)
        if left:
            print(f"유예 시간 {grace}초가 지나 처리 중인 요청 {left}개를 두고 종료한다", flush=True)
            os._exit(0)   # 왜: 멈춘 요청 스레드는 못 끊고, waitress의 정상 종료는 스레드를 5초 더 기다린다(유예 8초 + 5초 > docker stop 10초 뒤 SIGKILL)
        # 주 스레드의 srv.run()을 끝낸다: waitress의 트리거로 주 루프 안에서 소켓을 모두 닫으면 이벤트 맵이 비어 루프가 스스로 돌아온다.
        # 왜 신호(SIGINT)가 아닌가: 비대화형 셸의 `&` 작업과 E2E 하네스 아래에서는 SIGINT를 '무시'로 물려받아 raise_signal이 아무 일도 안 한다(U308 근본 원인, ADR 0022).
        # 왜 SystemExit이 아닌가: 트리거는 thunk의 예외를 모두 삼켜 기록만 한다(waitress trigger.py).
        srv.trigger.pull_trigger(lambda: close_all(srv))

    def stop(signum, frame):   # PID 1의 Python은 SIGTERM을 기본으로 무시한다. 새 연결을 받지 않고, 진행 중 요청을 유예 시간까지 기다린다
        print("종료 신호를 받았다. 새 요청을 받지 않고 진행 중 요청을 기다린다", flush=True)
        threading.Thread(target=finish, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    print("question-authoring 시작", flush=True)
    srv.run()
    srv.task_dispatcher.shutdown(cancel_pending=True, timeout=2)   # 루프가 끝난 뒤 남은 처리 스레드를 멈춘다(drain이 이미 비웠다)
    return 0


if __name__ == "__main__":
    sys.exit(main())
