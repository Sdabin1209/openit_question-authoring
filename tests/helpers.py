from question_authoring.service import Actor

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
    from question_authoring.http_api import make_server
    srv = make_server(api, "127.0.0.1", 0, **kw)
    threading.Thread(target=srv.run, daemon=True).start()

    def stop():
        srv.close(); srv.task_dispatcher.shutdown(cancel_pending=True, timeout=2)
    return srv.effective_port, stop


def stop_http(srv):
    """start_server를 쓰지 않고 make_server로 직접 띄운 waitress 서버를 닫는다."""
    srv.close(); srv.task_dispatcher.shutdown(cancel_pending=True, timeout=2)
