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
    threading.Thread(target=srv.run, daemon=True).start()

    def stop():
        srv.close(); srv.task_dispatcher.shutdown(cancel_pending=True, timeout=2)
    return srv.effective_port, stop


def stop_http(srv):
    """start_server를 쓰지 않고 make_server로 직접 띄운 waitress 서버를 닫는다."""
    srv.close(); srv.task_dispatcher.shutdown(cancel_pending=True, timeout=2)


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


def build_signals(source, *, question_set_id=None, review_status=None, max_cases=2000, confusion_min=2, now=None, deadline_seconds=None):
    """시험용: 읽기(link_failures_read)와 세기(signals_build)를 잇는다. 서비스의 build_signals와 같은 연결이다."""
    from datetime import datetime, timezone
    from question_authoring.components import signals_build
    from question_authoring.components.link_failures_read import FailureReader
    from question_authoring.wiring import schemas
    bundle = schemas.bind(FailureReader(source))({"max_cases": max_cases, "review_status": review_status,
                                                  "question_set_id": question_set_id, "deadline_seconds": deadline_seconds})
    return schemas.bind(signals_build)({"bundle": bundle, "question_set_id": question_set_id, "confusion_min": confusion_min,
                                        "now": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")})


def phi_warnings(data: bytes):
    import base64
    from question_authoring.components import phi_screen
    from question_authoring.wiring import schemas
    return schemas.bind(phi_screen)({"data_base64": base64.b64encode(data).decode(), "accept_warning": True})["warnings"]
