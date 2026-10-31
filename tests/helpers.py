import time

from question_authoring.wiring.service import Actor

AUTHOR = Actor("dr-kim", "question-author")
REVIEWER = Actor("dr-park", "clinical-reviewer")


def publish(svc, qsid="qs-main", text: bytes = None, edits=None, actor=AUTHOR, reviewed=True):
    """초안을 만들어 미리보기를 보고 확인해 새 버전을 만든다. ref를 돌려준다."""
    from conftest import example_text
    if text is not None or edits is None:
        out = svc.ingest_material(actor, qsid, "form.txt", text or example_text())
        did = out["draft_id"]
        assert out["status"] == "PARSED", out
    else:
        did = svc.create_draft(actor, qsid)["draft_id"]
    if edits:
        svc.apply_edits(actor, did, edits)
    pv = svc.get_preview(did)
    assert pv["ready_to_confirm"], (pv["open_flags"], pv["validation_errors"], pv["change"])
    ref = svc.confirm(actor, did, pv["preview_hash"])
    if reviewed and svc.require_two_person:      # 4-eyes(D88): 임상 근거 승인은 다른 주체의 검수 역할이 한다
        svc.add_review(REVIEWER, ref, decision="APPROVED", clinical_basis_approved=True)
    return ref


def start_server(api, **kw):
    """HTTP 서버를 스레드로 띄운다. (port, stop)을 돌려준다. 서버 구현이 바뀌어도 시험이 같게 쓰도록 한 곳에 둔다."""
    import threading
    from question_authoring.components.link_authoring_server import make_server
    srv = make_server(api, "127.0.0.1", 0, **kw)
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()

    def stop():
        stop_http(srv, th)
    return srv.effective_port, stop


def stop_http(srv, thread=None):
    """start_server를 쓰지 않고 make_server로 직접 띄운 waitress 서버를 닫는다.
    왜 srv.close()가 아닌가: 다른 스레드에서 소켓을 닫으면 서버 루프가 닫힌 소켓을 select해 죽거나 닫는 중에 연결을 받는 경쟁이 생긴다(부하에서 흔들림).
    서버 루프 안에서 닫고(link_authoring_server.close_all, __main__의 종료와 같은 방법) 루프가 끝나기를 기다려, 이 함수가 돌아온 뒤에는 아무도 듣지 않는다."""
    import time
    from question_authoring.components.link_authoring_server import close_all
    try:
        srv.trigger.pull_trigger(lambda: close_all(srv))
    except OSError:
        pass                                 # 이미 닫혔다(시험이 서버를 내렸고 fixture가 다시 내린다): 두 번 불러도 안전하다
    if thread is not None:
        thread.join(HANG_GUARD_SECONDS)              # 매달림 감시: 정상은 수 ms, 루프가 안 끝나는 결함이 있을 때만 이 마감에 닿는다
    else:
        wait_until(lambda: not srv._map, "서버 루프 종료")
    srv.task_dispatcher.shutdown(cancel_pending=True, timeout=HANG_GUARD_SECONDS)   # 매달림 감시


HANG_GUARD_SECONDS = 300.0
# 매달림 감시(U422): 시험이 자기 가짜에게서 신호를 기다리는 대기(Event.wait 등)의 바깥 마감. 정상이면 즉시(수 ms) 끝나고 코드가 진짜로 멈췄을 때만 걸린다.
# 판정 값이 아니다: 합격·불합격은 결과로 정하고 시간으로 정하지 않는다. 정상 경로의 수만 배라 부하로는 닿지 않는다. 시험마다 숫자를 쓰지 않고 이 상수 하나를 쓴다.


def wait_until(cond, what="조건", limit=HANG_GUARD_SECONDS):
    """조건이 참이 될 때까지 기다린다(순서를 시간이 아닌 조건으로 정한다). limit은 매달림 감시: 끝나지 않으면 무한정 기다리지 않게 한다."""
    end = time.monotonic() + limit           # 매달림 감시
    while not cond():
        assert time.monotonic() < end, f"{what}이 이루어지지 않는다(매달림 감시)"
        time.sleep(0.01)


def bank_hash(bank):
    from boundary_contracts import canonical
    return canonical.sha256_hash(bank)


class Validators:
    """시험용: 옛 Validators 모양(errors, check)을 영역의 스키마 레지스트리 위에 둔다."""

    def errors(self, name, record):
        if name == "QuestionBank":
            from question_authoring.components.bank_validate import question_bank_errors
            return question_bank_errors(record)
        from question_authoring.wiring import schemas
        return schemas.problems(name, record)

    def check(self, name, record):
        errs = self.errors(name, record)
        assert not errs, errs


def build_signals(source, *, question_set_id=None, review_status=None, max_cases=2000, confusion_min=2, now=None, deadline_seconds=None, clock=None):
    """시험용: 읽기(link_failures_read)와 세기(signals_build)를 잇는다. 서비스의 build_signals와 같은 연결이다."""
    from datetime import datetime, timezone
    from question_authoring.components import signals_build
    from question_authoring.components.link_failures_read import FailureReader
    from question_authoring.wiring import schemas
    bundle = schemas.bind(FailureReader(source, clock=clock or (lambda: 0.0)))({"max_cases": max_cases, "review_status": review_status,
                                                  "question_set_id": question_set_id, "deadline_seconds": deadline_seconds})
    return schemas.bind(signals_build)({"bundle": bundle, "question_set_id": question_set_id, "confusion_min": confusion_min,
                                        "now": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")})


def phi_warnings(data: bytes):
    import base64
    from question_authoring.components import phi_screen
    from question_authoring.wiring import schemas
    return schemas.bind(phi_screen)({"data_base64": base64.b64encode(data).decode(), "accept_warning": True})["warnings"]


def run_bounded(fn):
    """fn()을 스레드에서 돌려 결과를 돌려준다. 끝나지 않으면 실패한다(매달림 감시: HANG_GUARD_SECONDS). 걸린 시간은 재지 않는다 —
    계산량 결함(제곱 시간)은 '정상의 수천 배'로 드러나 이 마감에 걸리고, 기계 부하는 판정을 바꾸지 못한다."""
    import threading
    box = {}

    def work():
        try:
            box["r"] = fn()
        except BaseException as e:              # noqa: BLE001  스레드 안의 예외를 호출자에게 그대로 전한다
            box["e"] = e
    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(HANG_GUARD_SECONDS)                         # 매달림 감시
    assert not th.is_alive(), "끝나지 않는다(계산량 결함 의심, 매달림 감시)"
    if "e" in box:
        raise box["e"]
    return box["r"]


class FakeMonotonic:
    """시험이 쥔 단조 시계(U422). 시간은 advance()를 부를 때만 흐른다."""
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, secs):
        self.now += secs
