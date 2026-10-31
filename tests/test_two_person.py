"""D88 U9: 저자 != 검수자(4-eyes)가 기본. 초안 소유, 엄격한 입력 형식, gateway 경유 인증, 동시 편집, 번호 예약 풀림, 환자 정보 탐지."""
from helpers import phi_warnings
import itertools
import json
import threading

import pytest
from boundary_contracts.errors import BoundaryError

from conftest import example_text
from question_authoring.components.link_authoring_server import Api
from question_authoring.wiring.service import Actor, AuthoringService
from question_authoring.wiring.stores import MemoryQuestionStore

KIM = Actor("dr-kim", "question-author")
LEE = Actor("dr-lee", "question-author")
PARK = Actor("dr-park", "clinical-reviewer")
KIM_AS_REVIEWER = Actor("dr-kim", "clinical-reviewer")      # 같은 주체가 검수 역할을 받은 경우


@pytest.fixture
def s2(validators):
    c = itertools.count(1)
    return AuthoringService(MemoryQuestionStore(), new_id=lambda p: f"{p}-{next(c):04d}")   # 기본값 = 4-eyes


def draft_ready(s, actor=KIM, qsid="qs-main"):
    did = s.ingest_material(actor, qsid, "f.txt", example_text())["draft_id"]
    return did, s.get_preview(did)["preview_hash"]


def code(fn, *a, **k):
    with pytest.raises(BoundaryError) as e:
        fn(*a, **k)
    return e.value.code


def test_default_is_two_person_and_author_confirm_is_not_clinical_approval(s2):
    assert s2.require_two_person is True
    did, ph = draft_ready(s2)
    ref = s2.confirm(KIM, did, ph)
    rv = s2.store.get_reviews(ref)
    assert len(rv) == 1 and rv[0]["clinical_basis_approved"] is False and rv[0]["reviewer_id"] == "dr-kim"
    assert code(s2.store.reader().latest, "qs-main", reviewed_only=True) == "NOT_FOUND"      # 아직 검수 완료가 아니다
    s2.add_review(PARK, ref, decision="APPROVED", clinical_basis_approved=True)
    assert s2.store.reader().latest("qs-main", reviewed_only=True) == ref


def test_same_subject_cannot_approve_own_version(s2):
    did, ph = draft_ready(s2)
    ref = s2.confirm(KIM, did, ph)
    assert code(s2.add_review, KIM_AS_REVIEWER, ref, decision="APPROVED", clinical_basis_approved=True) == "PERMISSION_DENIED"
    assert code(s2.add_review, KIM, ref, decision="APPROVED", clinical_basis_approved=True) == "PERMISSION_DENIED"   # 역할도 아님
    assert code(s2.add_review, LEE, ref, decision="APPROVED", clinical_basis_approved=True) == "PERMISSION_DENIED"   # 다른 저자도 검수 역할이 아니다
    s2.add_review(KIM, ref, decision="REJECTED", clinical_basis_approved=False, reason="철회")                    # 철회는 저자도 할 수 있다


def test_draft_ownership(s2):
    did, ph = draft_ready(s2)
    e = [{"op": "set", "path": "/questions/0/name", "value": "x"}]
    assert code(s2.apply_edits, LEE, did, e) == "PERMISSION_DENIED"
    assert code(s2.confirm, LEE, did, ph) == "PERMISSION_DENIED"
    assert code(s2.discard_draft, LEE, did, "r") == "PERMISSION_DENIED"
    assert code(s2.acknowledge_flags, LEE, did, []) == "PERMISSION_DENIED"
    s2.apply_edits(KIM, did, e)


def test_reviewer_cannot_author(s2):
    did, ph = draft_ready(s2)
    assert code(s2.ingest_material, PARK, "qs-x", "f.txt", example_text()) == "PERMISSION_DENIED"
    assert code(s2.confirm, PARK, did, ph) == "PERMISSION_DENIED"
    assert code(s2.create_draft, PARK, "qs-x") == "PERMISSION_DENIED"
    s2.require_reader(PARK)


def test_flag_false_restores_old_behavior_and_warns(validators):
    s = AuthoringService(MemoryQuestionStore(), require_two_person=False)
    did, ph = draft_ready(s)
    ref = s.confirm(KIM, did, ph)
    assert s.store.get_reviews(ref)[0]["clinical_basis_approved"] is True
    api = Api(s, {"t": KIM}, warnings=["two_person_disabled"])
    st, body = api.handle("GET", "/readyz", {})
    assert st == 200 and body["warnings"] == ["two_person_disabled"]
    assert "warnings" not in Api(s, {"t": KIM}).handle("GET", "/readyz", {})[1]


def test_strict_input_types_over_http(s2):
    api = Api(s2, {"k": KIM, "p": PARK})
    H = {"Authorization": "Bearer p"}
    did, ph = draft_ready(s2)
    ref = s2.confirm(KIM, did, ph)
    for bad in ("false", "no", 0, 1, None):
        st, body = api.handle("POST", "/v1/reviews", H, json.dumps(
            {"question_set": ref, "decision": "APPROVED", "clinical_basis_approved": bad}).encode())
        assert st == 400, (bad, st)
    st, _ = api.handle("POST", "/v1/reviews", H, json.dumps(
        {"question_set": ref, "decision": "APPROVED", "clinical_basis_approved": True}).encode())
    assert st == 200
    K = {"Authorization": "Bearer k"}
    did2 = s2.create_draft(KIM, "qs-main")["draft_id"]
    for body in ({"edits": "x"}, {"edits": [1]}, {"edits": {"a": 1}}):
        assert api.handle("POST", f"/v1/drafts/{did2}/edits", K, json.dumps(body).encode())[0] == 400
    assert api.handle("POST", f"/v1/drafts/{did2}/discard", K, json.dumps({"reason": 5}).encode())[0] == 400


def test_gateway_identity(s2):
    api = Api(s2, {"static": KIM}, gateway_tokens={"gw-tok"}, user_roles={"u-kim": "question-author", "u-park": "clinical-reviewer"})
    def call(tok, user=None, path="/v1/drafts", method="GET", body=b""):
        h = {"Authorization": f"Bearer {tok}"}
        if user:
            h["X-Openit-User"] = user
        return api.handle(method, path, h, body)[0]
    assert call("gw-tok", "u-kim") == 200
    assert call("gw-tok") == 401                       # 헤더 없음(사람 행위자 신원이 없다)
    assert call("gw-tok", "stranger") == 403           # 모르는 사용자
    assert call("gw-tok", "u-park") == 200             # 검수 역할은 조회 가능
    assert call("gw-tok", "u-park", "/v1/materials?question_set_id=qs-g&filename=f.txt", "POST", example_text()) == 403
    assert call("gw-tok", "u-kim", "/v1/materials?question_set_id=qs-g&filename=f.txt", "POST", example_text()) == 200
    # 정적 토큰은 헤더를 무시한다(클라이언트가 보낸 X-Openit-User를 믿지 않는다)
    h = {"Authorization": "Bearer static", "X-Openit-User": "u-park"}
    assert api.handle("POST", "/v1/materials?question_set_id=qs-h&filename=f.txt", h, example_text())[0] == 200
    assert s2.list_drafts("qs-h")[0]["created_by"] == "dr-kim"
    # 게이트웨이 토큰이 아닌 임의 토큰 + 헤더
    assert call("other", "u-kim") == 401   # gateway 토큰이 아닌 임의 토큰은 인증 실패


def test_confirming_reservation_is_released_on_conflict(s2):
    d_a, ph_a = draft_ready(s2, KIM, "qs-r")
    d_b, _ = draft_ready(s2, LEE, "qs-r")
    s2.apply_edits(LEE, d_b, [{"op": "set", "path": "/questions/0/name", "value": "다른 이름"}])   # 내용이 달라야 같은 번호 충돌이 된다
    ph_b = s2.get_preview(d_b)["preview_hash"]
    real = s2.store.commit_version
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise BoundaryError("UNAVAILABLE", "일시 장애")        # 예약만 남기고 중단된 확인
        return real(*a, **k)
    s2.store.commit_version = flaky
    assert code(s2.confirm, KIM, d_a, ph_a) == "UNAVAILABLE"
    assert s2.get_draft(d_a)["confirming"]["version"] == 1
    s2.confirm(LEE, d_b, ph_b)                                    # 다른 초안이 1번을 가져갔다
    assert code(s2.confirm, KIM, d_a, ph_a) == "FAILED_PRECONDITION"
    assert "confirming" not in s2.get_draft(d_a)                  # 예약이 풀렸다(이전에는 영영 CONFLICT)


def test_concurrent_edits_are_not_lost(s2):
    did = s2.create_draft(KIM, "qs-c", base_version=None)["draft_id"]
    errs = []

    def edit(i):
        try:
            s2.apply_edits(KIM, did, [{"op": "set", "path": f"/ext_{i}", "value": i}])
        except Exception as e:  # pragma: no cover
            errs.append(e)
    ts = [threading.Thread(target=edit, args=(i,)) for i in range(25)]
    [t.start() for t in ts]; [t.join() for t in ts]
    bank = s2.get_draft(did)["bank"]
    assert not errs and all(bank.get(f"ext_{i}") == i for i in range(25))


@pytest.mark.parametrize("text,expect", [
    ("홍길동900101-1234567 입니다", "주민등록번호 형태"), ("9001011234567", "주민등록번호 형태"),
    ("900101 1234567", "주민등록번호 형태"), ("９００１０１－１２３４５６７", "주민등록번호 형태"),
    ("010 1234 5678", "전화번호 형태"), ("01012345678", "전화번호 형태"), ("+82-10-1234-5678", "전화번호 형태"),
    ("+82 10 1234 5678", "전화번호 형태"), ("연락처010.1234.5678", "전화번호 형태"),
    ("02-123-4567", "유선전화 형태"), ("031 123 4567", "유선전화 형태"), ("a.b@c.kr", "이메일 형태"),
])
def test_phi_detection_widened(text, expect):
    assert expect in phi_warnings(text.encode())


@pytest.mark.parametrize("text", ["체온 36.5도", "순서: 1", "확인 한도: 3", "Q1.s1 = 예", "2026-10-28", "한도 30"])
def test_phi_no_false_alarm_on_normal_form_text(text):
    assert phi_warnings(text.encode()) == []


def test_example_form_has_no_phi_warning():
    assert phi_warnings(example_text()) == []


def test_non_utf8_is_not_silently_accepted(s2):
    data = "문항 Q1: 체온\n010-1234-5678".encode("utf-16")
    assert phi_warnings(data) and "UTF-8" in phi_warnings(data)[0]
    assert code(s2.ingest_material, KIM, "qs-u", "f.txt", data) == "FAILED_PRECONDITION"
    out = s2.ingest_material(KIM, "qs-u", "f.txt", data, accept_phi_warning=True)
    assert out["status"] == "FALLBACK"


@pytest.mark.parametrize("text", ["2024-01-02 1234567", "2024.01.02 1234567", "20240102 1234567", "작성일 2026/10/28 1234567"])
def test_dates_do_not_trigger_phone_warnings(text):
    assert phi_warnings(text.encode()) == []


def test_date_exclusion_keeps_real_detections():
    for t in ("2001011234567", "홍길동900101-1234567", "010 1234 5678", "2024-01-02 연락처 010-1234-5678", "+82-10-1234-5678"):
        assert phi_warnings(t.encode()), t


# 라운드3 M2: 날짜꼴로 끝나는 전화번호는 날짜 치환에 지워지지 않고 잡혀야 한다. 날짜 문장은 계속 경고가 없어야 한다
@pytest.mark.parametrize("text,expect", [
    ("010-20120101", "전화번호 형태"), ("+82 10 20120101", "전화번호 형태"), ("02-20120101", "유선전화 형태"),
    ("031-19991231", "유선전화 형태"), ("01012345678", "전화번호 형태"), ("010-2012-0101", "전화번호 형태"),
    ("(02) 2012-0101", "유선전화 형태"), ("(031)-1999-1231", "유선전화 형태"), ("(02)20120101", "유선전화 형태"),
    ("+82-2-20120101", "유선전화 형태"), ("연락처 010 2012 0101", "전화번호 형태"),
])
def test_phone_ending_like_date_is_detected(text, expect):
    assert expect in phi_warnings(text.encode())


@pytest.mark.parametrize("text", ["2012-01-01", "2012.01.01", "20120101에 방문", "방문일 2012/01/01 입니다",
                                  "2024-01-02 2024-01-03 1234567"])
def test_plain_dates_still_not_phone(text):
    assert phi_warnings(text.encode()) == []


def test_phi_check_finishes_on_phone_and_date_text():
    """전화번호·날짜 꼴이 길게 이어져도 정규식이 제곱 시간으로 느려지지 않는다. 시간을 재지 않고 끝나는지만 본다(U421):
    제곱이면 수십만 자에서 수 시간이라 매달림 감시(HANG_GUARD_SECONDS)에 걸린다. 정상은 수십 ms."""
    from helpers import run_bounded
    unit = "방문 2012-01-01 010-2012 0101 20120101 02-".encode()
    assert run_bounded(lambda: phi_warnings(unit * 8000)) is not None
