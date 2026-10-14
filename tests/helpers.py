from question_authoring.service import Actor

AUTHOR = Actor("dr-kim", "question-author")


def publish(svc, qsid="qs-main", text: bytes = None, edits=None, actor=AUTHOR):
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
    return svc.confirm(actor, did, pv["preview_hash"])
