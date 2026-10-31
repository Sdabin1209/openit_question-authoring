"""문항 저장소 컴포넌트. I1 QuestionReader의 읽기 논리와 보존·정리(purge)는 여기 한 곳에 있고, 백엔드(메모리, SQLite, PostgreSQL)는
원시 연산과 쓰기 연산만 낸다(wiring이 주입).

쓰기 연산(commit_version, add_review, 초안, 원본 자료)은 저작 서비스만 부른다. 이웃 영역에는 읽기 전용 `reader()`
뷰(QuestionReader)만 내보낸다.
"""
from __future__ import annotations

import copy
from typing import Optional

from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError

# 쪽 하나의 최대 크기. HTTP 읽기 면은 limit을 500까지만 받는다(link_authoring_server._page). 이 값(1000)은 저장소를 직접 부르는 호출(이웃 시험의 메모리 구현 등)이
# 한 번에 메모리를 크게 쥐지 않게 하는 방어 상한이다. 측정한 값이 아니라 HTTP 상한의 두 배로 잡은 판단값이다(근거 기록 없음, D89 한계)
MAX_LIMIT = 1000


def bank_hash(bank: dict) -> str:
    return canonical.sha256_hash(bank)


class QuestionReaderView:
    """읽기 전용 뷰. 저장소 객체의 쓰기 메서드를 드러내지 않는다."""

    def __init__(self, store: "QuestionStore"):
        self._s = store

    def ref_of(self, question_set_id, version): return self._s.ref_of(question_set_id, version)
    def get_question_bank(self, ref): return self._s.get_question_bank(ref)
    def latest(self, question_set_id, *, reviewed_only=False): return self._s.latest(question_set_id, reviewed_only=reviewed_only)
    def list_ids(self, *, cursor=None, limit=100): return self._s.list_ids(cursor=cursor, limit=limit)
    def list_versions(self, question_set_id, *, cursor=None, limit=100): return self._s.list_versions(question_set_id, cursor=cursor, limit=limit)
    def get_reviews(self, ref): return self._s.get_reviews(ref)
    def get_change(self, ref): return self._s.get_change(ref)


class QuestionStore:
    """문항 저장소 컴포넌트: I1 읽기 논리, 보존·정리(purge), 쓰기 연산의 창구.

    왜 합성(composition)인가: 컴포넌트끼리 import할 수 없어(구조 규칙 3) 백엔드(메모리·SQLite·PostgreSQL)가 이 클래스를 상속할 수 없다.
    백엔드는 wiring이 주입한다. 백엔드가 내는 원시 연산은 아래 `_PRIMITIVES`와 쓰기·초안·원본 연산이다.
    """

    _PRIMITIVES = ("_version_rows", "_ids", "_bank_json", "_review_rows", "_change_row", "_delete_draft_if_unchanged",
                   "_version_source_hashes", "_blob_meta", "_stamp_blobs", "_delete_blob_if_stale")

    def __init__(self, backend, check):
        """check(스키마 이름, 값): wiring이 주입하는 스키마 검증. 읽기 요청(ref, 쪽 매기기)의 모양은 손 검사가 아니라 이것으로 본다."""
        self._b = backend
        self._check = check

    def __getattr__(self, name):
        # 쓰기·초안·원본 연산(commit_version, add_review, save_draft, load_draft, list_drafts, put_source, get_source, close, is_ready, kind ...)과
        # 백엔드 고유 속성은 그대로 백엔드로 넘긴다. 왜: 원시 연산 모양은 백엔드 컴포넌트가 정하고, 이 컴포넌트는 읽기 논리만 소유한다
        if name.startswith("__") or name == "_b":
            raise AttributeError(name)
        return getattr(self._b, name)

    def __setattr__(self, name, value):
        # 백엔드가 가진 속성을 바꾸면(시험이 연결 함수 등을 갈아 끼울 때) 백엔드에 반영한다. 이 클래스 자신의 속성(_b, _check)만 여기에 둔다
        if name in ("_b", "_check") or name in self._PRIMITIVES or not hasattr(self.__dict__.get("_b"), name):
            object.__setattr__(self, name, value)
        else:
            setattr(self._b, name, value)

    def close(self) -> None:
        c = getattr(self._b, "close", None)
        if c:
            c()

    def _version_rows(self, qsid): return self._b._version_rows(qsid)
    def _ids(self): return self._b._ids()
    def _bank_json(self, qsid, n): return self._b._bank_json(qsid, n)
    def _review_rows(self, qsid, n): return self._b._review_rows(qsid, n)
    def _change_row(self, qsid, n): return self._b._change_row(qsid, n)
    def _delete_draft_if_unchanged(self, d): return self._b._delete_draft_if_unchanged(d)
    def _version_source_hashes(self): return self._b._version_source_hashes()
    def _blob_meta(self): return self._b._blob_meta()
    def _stamp_blobs(self, now): return self._b._stamp_blobs(now)
    def _delete_blob_if_stale(self, h, before): return self._b._delete_blob_if_stale(h, before)

    # ---- 보존·정리(D88 U4): 처리되지 않은 초안과, 어떤 버전·초안도 참조하지 않는 원본 자료만 지운다. 버전·검수·변경 분류는 불변이라 지우지 않는다 ----
    def purge(self, *, now: str, draft_before: str, source_before: str) -> dict:
        """멱등. draft_before/source_before보다 오래된 것만 지운다(ISO UTC 문자열 비교). 값은 호출자가 정한다(보존 기간은 설정)."""
        self._stamp_blobs(now)
        removed_drafts = 0
        for d in self.list_drafts():
            if d.get("status") in ("OPEN", "DISCARDED") and str(d.get("updated_at", "")) < draft_before:
                if self._delete_draft_if_unchanged(d):
                    removed_drafts += 1
        removed_blobs = 0
        for h, created in self._blob_meta():
            if created is not None and created < source_before and self._delete_blob_if_stale(h, source_before):
                removed_blobs += 1
        return {"drafts_removed": removed_drafts, "sources_removed": removed_blobs}

    # ---- I1 읽기 논리 ----
    def reader(self) -> "QuestionReaderView":
        return QuestionReaderView(self)

    @staticmethod
    def _vno(version: str) -> int:
        try:
            n = int(version)
        except (TypeError, ValueError):
            n = -1
        if n < 1 or str(n) != version:
            raise BoundaryError("NOT_FOUND", f"버전이 없다: {version}")
        return n

    def _locate(self, ref) -> tuple[str, int, str, dict]:
        self._check("StoreRefQuery", ref)
        qsid, version, want_hash = ref["id"], ref["version"], ref["content_hash"]
        n = self._vno(version)
        bank = self._bank_json(qsid, n)
        if bank is None:
            raise BoundaryError("NOT_FOUND", f"문항 세트 버전이 없다: {qsid}@{version}")
        return qsid, n, want_hash, bank

    def ref_of(self, question_set_id: str, version: str) -> dict:
        """버전 번호로 ArtifactRef를 얻는다. 없으면 NOT_FOUND. (HTTP 읽기 면이 경로의 버전을 ref로 바꿀 때 쓴다.)"""
        n = self._vno(version)
        h = dict(self._version_rows(question_set_id)).get(n)
        if h is None:
            raise BoundaryError("NOT_FOUND", f"문항 세트 버전이 없다: {question_set_id}@{version}")
        return self._ref(question_set_id, n, h)

    def get_question_bank(self, ref) -> dict:
        qsid, n, want_hash, bank = self._locate(ref)
        stored = dict(self._version_rows(qsid))[n]
        actual = bank_hash(bank)
        if actual != stored:
            raise BoundaryError("INTEGRITY_ERROR", f"저장된 내용의 해시가 기록과 다르다: {qsid}@{n}")
        if want_hash != actual:
            raise BoundaryError("INTEGRITY_ERROR", f"ref의 content_hash가 내용과 다르다: {qsid}@{n}")
        return bank

    def _ref(self, qsid: str, n: int, h: str) -> dict:
        return {"id": qsid, "version": str(n), "content_hash": h}

    def _approved(self, qsid: str, n: int) -> bool:
        """가장 최근 검수가 APPROVED이면서 clinical_basis_approved인 버전. 이후의 반려는 승인을 거둔다(design.md 5.3)."""
        reviews = self._review_rows(qsid, n)
        if not reviews:
            return False
        last = max(enumerate(reviews), key=lambda t: (t[1]["reviewed_at"], t[0]))[1]
        return last["decision"] == "APPROVED" and last["clinical_basis_approved"] is True

    def latest(self, question_set_id: str, *, reviewed_only: bool = False) -> dict:
        rows = self._version_rows(question_set_id)
        for n, h in reversed(rows):
            if not reviewed_only or self._approved(question_set_id, n):
                return self._ref(question_set_id, n, h)
        raise BoundaryError("NOT_FOUND", f"문항 세트에 해당하는 버전이 없다: {question_set_id}"
                            + (" (검수 완료 기준)" if reviewed_only else ""))

    def list_ids(self, *, cursor: Optional[str] = None, limit: int = 100):
        self._check("StorePageQuery", {"limit": limit, "cursor": cursor})
        limit = min(limit, MAX_LIMIT)
        ids = [i for i in self._ids() if cursor is None or i > cursor]
        page = ids[:limit]
        return page, (page[-1] if len(ids) > limit else None)

    def list_versions(self, question_set_id: str, *, cursor: Optional[str] = None, limit: int = 100):
        self._check("StoreVersionPageQuery", {"limit": limit, "cursor": cursor})
        limit = min(limit, MAX_LIMIT)
        after = int(cursor) if cursor is not None else 0
        rows = [(n, h) for n, h in self._version_rows(question_set_id) if n > after]
        page = rows[:limit]
        nxt = str(page[-1][0]) if len(rows) > limit else None
        return [self._ref(question_set_id, n, h) for n, h in page], nxt

    def get_reviews(self, ref) -> list[dict]:
        qsid, n, _, _ = self._locate(ref)
        rows = list(enumerate(self._review_rows(qsid, n)))
        rows.sort(key=lambda t: (t[1]["reviewed_at"], t[0]))
        return [copy.deepcopy(r) for _, r in rows]

    def get_change(self, ref) -> Optional[dict]:
        qsid, n, _, _ = self._locate(ref)
        return self._change_row(qsid, n)
