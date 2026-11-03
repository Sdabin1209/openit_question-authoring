"""RUNTIME R10.8·CONVENTIONS 4절: 저작 쓰기(문진표 확정)는 같은 문진표 ID·버전이면 같은 내용 200(기존 것), 다른 내용 409 CONFLICT.
동시에 도착한 두 쓰기는 하나가 만들고 나머지는 같은 내용이면 200, 다르면 409 이며 503 이 아니다. HTTP 로 상태 코드 + 본문을 단언한다."""
import json
import threading

import pytest

from conftest import example_text
from question_authoring.components.link_authoring_server import Api
from question_authoring.wiring.service import Actor

KIM = Actor("dr-kim", "question-author")
LEE = Actor("dr-lee", "question-author")
H1 = {"Authorization": "Bearer t-kim"}
H2 = {"Authorization": "Bearer t-lee"}


def api_of(svc):
    return Api(svc, {"t-kim": KIM, "t-lee": LEE})


def call(api, method, path, h, body=None, raw=None):
    st, out = api.handle(method, path, h, raw if raw is not None else (b"" if body is None else json.dumps(body).encode()))
    return st, out


def draft(api, h, qsid, edit=None):
    st, out = call(api, "POST", f"/v1/materials?question_set_id={qsid}&filename=f.txt", h, raw=example_text(qsid))
    assert st == 200, out
    did = out["draft_id"]
    if edit:
        assert call(api, "POST", f"/v1/drafts/{did}/edits", h, {"edits": [{"op": "set", "path": "/questions/0/name", "value": edit}]})[0] == 200
    return did, call(api, "GET", f"/v1/drafts/{did}/preview", h)[1]["preview_hash"]


def test_same_confirm_again_is_200_with_the_same_ref(svc):
    api = api_of(svc)
    did, ph = draft(api, H1, "qs-i")
    st1, r1 = call(api, "POST", f"/v1/drafts/{did}/confirm", H1, {"preview_hash": ph})
    st2, r2 = call(api, "POST", f"/v1/drafts/{did}/confirm", H1, {"preview_hash": ph})
    assert (st1, st2) == (200, 200) and r1 == r2 and r1["version"] == "1"


def test_same_draft_with_different_content_hash_is_409(svc):
    api = api_of(svc)
    did, ph = draft(api, H1, "qs-j")
    assert call(api, "POST", f"/v1/drafts/{did}/confirm", H1, {"preview_hash": ph})[0] == 200
    st, out = call(api, "POST", f"/v1/drafts/{did}/confirm", H1, {"preview_hash": "sha256:" + "0" * 64})
    assert st == 409 and out["code"] == "CONFLICT" and out["retryable"] is False


def test_two_drafts_reserving_the_same_version_with_different_content_second_gets_409_not_503(svc):
    """동시 도착: 둘 다 1번을 예약하고 한쪽이 먼저 쓴다. 나머지는 같은 ID·버전에 다른 내용이라 409 CONFLICT(R10.8)."""
    api = api_of(svc)
    da, pa = draft(api, H1, "qs-k")
    db, pb = draft(api, H2, "qs-k", edit="다른 이름")
    real = svc.store.commit_version
    gate = threading.Barrier(2, timeout=30)           # 매달림 감시: 둘이 커밋 직전에 만난다

    def meet(*a, **k):
        gate.wait()
        return real(*a, **k)
    svc.store.commit_version = meet
    res = {}

    def go(name, did, ph, h):
        res[name] = call(api, "POST", f"/v1/drafts/{did}/confirm", h, {"preview_hash": ph})
    ts = [threading.Thread(target=go, args=("a", da, pa, H1)), threading.Thread(target=go, args=("b", db, pb, H2))]
    [t.start() for t in ts]; [t.join(60) for t in ts]
    codes = sorted(st for st, _ in res.values())
    assert codes == [200, 409], res
    loser = next(o for st, o in res.values() if st == 409)
    assert loser["code"] == "CONFLICT" and loser["retryable"] is False
    assert len(svc.store.reader().list_versions("qs-k")[0]) == 1          # 흔적 없음: 한 버전만 있다


def test_two_drafts_with_identical_content_both_get_200_and_one_version(svc):
    api = api_of(svc)
    da, pa = draft(api, H1, "qs-l")
    db, pb = draft(api, H2, "qs-l")
    real = svc.store.commit_version
    gate = threading.Barrier(2, timeout=30)

    def meet(*a, **k):
        gate.wait()
        return real(*a, **k)
    svc.store.commit_version = meet
    res = {}
    ts = [threading.Thread(target=lambda n, d, p, h: res.__setitem__(n, call(api, "POST", f"/v1/drafts/{d}/confirm", h, {"preview_hash": p})), args=a)
          for a in (("a", da, pa, H1), ("b", db, pb, H2))]
    [t.start() for t in ts]; [t.join(60) for t in ts]
    assert sorted(st for st, _ in res.values()) == [200, 200], res
    assert res["a"][1] == res["b"][1]                                        # 같은 ref
    assert len(svc.store.reader().list_versions("qs-l")[0]) == 1
