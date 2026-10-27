"""문항 저장소 추상. I1 QuestionReader의 읽기 논리는 여기 한 곳에 있고, 구현(메모리, SQLite)은 원시 연산만 낸다.

쓰기 연산(commit_version, add_review, 초안, 원본 자료)은 저작 서비스만 부른다. 이웃 영역에는 읽기 전용 `reader()`
뷰(QuestionReader)만 내보낸다.
"""
from __future__ import annotations

import abc
import copy
from typing import Optional

from boundary_contracts import canonical
from boundary_contracts.errors import BoundaryError

MAX_LIMIT = 1000


def bank_hash(bank: dict) -> str:
    return canonical.sha256_hash(bank)


def check_ref(ref) -> tuple[str, str, str]:
    if not isinstance(ref, dict) or set(ref) != {"id", "version", "content_hash"} or \
            not all(isinstance(ref[k], str) for k in ref):
        raise BoundaryError("INVALID_RECORD", "ArtifactRef 형식이 아니다")
    return ref["id"], ref["version"], ref["content_hash"]


class QuestionReaderView:
    """읽기 전용 뷰. 저장소 객체의 쓰기 메서드를 드러내지 않는다."""

    def __init__(self, store: "AuthoringStore"):
        self._s = store

    def ref_of(self, question_set_id, version): return self._s.ref_of(question_set_id, version)
    def get_question_bank(self, ref): return self._s.get_question_bank(ref)
    def latest(self, question_set_id, *, reviewed_only=False): return self._s.latest(question_set_id, reviewed_only=reviewed_only)
    def list_ids(self, *, cursor=None, limit=100): return self._s.list_ids(cursor=cursor, limit=limit)
    def list_versions(self, question_set_id, *, cursor=None, limit=100): return self._s.list_versions(question_set_id, cursor=cursor, limit=limit)
    def get_reviews(self, ref): return self._s.get_reviews(ref)
    def get_change(self, ref): return self._s.get_change(ref)


class AuthoringStore(abc.ABC):
    # ---- 원시 연산(구현이 낸다). 반환값은 호출자가 고쳐도 저장소에 영향이 없는 복사본이어야 한다 ----
    @abc.abstractmethod
    def _version_rows(self, qsid: str) -> list[tuple[int, str]]:
        """[(version_no, content_hash)] 오름차순."""
    @abc.abstractmethod
    def _ids(self) -> list[str]:
        """문항 세트 ID 오름차순."""
    @abc.abstractmethod
    def _bank_json(self, qsid: str, version_no: int) -> Optional[dict]: ...
    @abc.abstractmethod
    def _review_rows(self, qsid: str, version_no: int) -> list[dict]:
        """삽입 순서."""
    @abc.abstractmethod
    def _change_row(self, qsid: str, version_no: int) -> Optional[dict]: ...
    @abc.abstractmethod
    def commit_version(self, bank: dict, ref: dict, review: dict, change: Optional[dict], source: Optional[dict]) -> None:
        """한 트랜잭션으로 버전, 첫 검수, 변경 분류를 쓴다. 번호는 마지막 번호 + 1이어야 한다(아니면 CONFLICT).
        같은 (id, version)이 이미 있으면 같은 해시일 때만 조용히 성공(멱등), 다르면 CONFLICT."""
    @abc.abstractmethod
    def add_review(self, review: dict) -> None:
        """기존 버전에 검수를 덧붙인다. 같은 review_id는 같은 내용일 때만 성공(멱등)."""
    # 초안과 원본 자료(가변 작업 영역. 버전이 아니다)
    @abc.abstractmethod
    def save_draft(self, draft: dict) -> None: ...
    @abc.abstractmethod
    def load_draft(self, draft_id: str) -> Optional[dict]: ...
    @abc.abstractmethod
    def list_drafts(self, question_set_id: Optional[str] = None) -> list[dict]: ...
    @abc.abstractmethod
    def put_source(self, source_hash: str, filename: str, data: bytes) -> None: ...
    @abc.abstractmethod
    def get_source(self, source_hash: str) -> Optional[tuple[str, bytes]]: ...
    def close(self) -> None: ...

    # ---- 보존·정리(D88 U4): 처리되지 않은 초안과, 어떤 버전·초안도 참조하지 않는 원본 자료만 지운다. 버전·검수·변경 분류는 불변이라 지우지 않는다 ----
    @abc.abstractmethod
    def _delete_draft(self, draft_id: str) -> None: ...
    @abc.abstractmethod
    def _version_source_hashes(self) -> set: ...
    @abc.abstractmethod
    def _blob_meta(self) -> list:
        """[(source_hash, created_at 또는 None)]."""
    @abc.abstractmethod
    def _stamp_blobs(self, now: str) -> None:
        """저장 시각을 모르는(이 기능 이전의) 원본 자료에 지금 시각을 적는다. 그때부터 보존 기간이 센다."""
    @abc.abstractmethod
    def _delete_blob(self, source_hash: str) -> None: ...

    def purge(self, *, now: str, draft_before: str, source_before: str) -> dict:
        """멱등. draft_before/source_before보다 오래된 것만 지운다(ISO UTC 문자열 비교). 값은 호출자가 정한다(보존 기간은 설정)."""
        self._stamp_blobs(now)
        removed_drafts = 0
        for d in self.list_drafts():
            if d.get("status") in ("OPEN", "DISCARDED") and str(d.get("updated_at", "")) < draft_before:
                self._delete_draft(d["draft_id"])
                removed_drafts += 1
        keep = set(self._version_source_hashes())
        for d in self.list_drafts():
            h = (d.get("source") or {}).get("source_hash")
            if h:
                keep.add(h)
        removed_blobs = 0
        for h, created in self._blob_meta():
            if h not in keep and created is not None and created < source_before:
                self._delete_blob(h)
                removed_blobs += 1
        return {"drafts_removed": removed_drafts, "sources_removed": removed_blobs}

    # ---- I1 읽기 논리 ----
    def reader(self) -> QuestionReaderView:
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
        qsid, version, want_hash = check_ref(ref)
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
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise BoundaryError("INVALID_RECORD", "limit은 1 이상의 정수여야 한다")
        limit = min(limit, MAX_LIMIT)
        if cursor is not None and not isinstance(cursor, str):
            raise BoundaryError("INVALID_RECORD", "cursor 형식이 아니다")
        ids = [i for i in self._ids() if cursor is None or i > cursor]
        page = ids[:limit]
        return page, (page[-1] if len(ids) > limit else None)

    def list_versions(self, question_set_id: str, *, cursor: Optional[str] = None, limit: int = 100):
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise BoundaryError("INVALID_RECORD", "limit은 1 이상의 정수여야 한다")
        limit = min(limit, MAX_LIMIT)
        after = 0
        if cursor is not None:
            try:
                after = int(cursor)
            except (TypeError, ValueError):
                raise BoundaryError("INVALID_RECORD", "cursor 형식이 아니다")
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
