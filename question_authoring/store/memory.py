"""메모리 구현(가짜 구현). SQLite 구현과 같은 적합성 시험을 통과한다."""
from __future__ import annotations

import copy
import threading
from typing import Optional

from boundary_contracts.errors import BoundaryError
from .base import AuthoringStore, bank_hash


def _now_ts() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class MemoryQuestionStore(AuthoringStore):
    def __init__(self):
        self._lock = threading.RLock()
        self._banks: dict[tuple[str, int], dict] = {}
        self._hashes: dict[tuple[str, int], str] = {}
        self._reviews: dict[tuple[str, int], list[dict]] = {}
        self._changes: dict[tuple[str, int], dict] = {}
        self._sources_prov: dict[tuple[str, int], dict] = {}
        self._drafts: dict[str, dict] = {}
        self._blobs: dict[str, tuple[str, bytes]] = {}
        self._review_ids: dict[str, dict] = {}
        self._blob_times: dict[str, Optional[str]] = {}

    def _version_rows(self, qsid):
        with self._lock:
            return sorted((n, h) for (q, n), h in self._hashes.items() if q == qsid)

    def _ids(self):
        with self._lock:
            return sorted({q for q, _ in self._hashes})

    def _bank_json(self, qsid, n):
        with self._lock:
            b = self._banks.get((qsid, n))
            return copy.deepcopy(b) if b is not None else None

    def _review_rows(self, qsid, n):
        with self._lock:
            return copy.deepcopy(self._reviews.get((qsid, n), []))

    def _change_row(self, qsid, n):
        with self._lock:
            c = self._changes.get((qsid, n))
            return copy.deepcopy(c) if c is not None else None

    def commit_version(self, bank, ref, review, change, source):
        qsid, n = ref["id"], int(ref["version"])
        with self._lock:
            if bank_hash(bank) != ref["content_hash"]:
                raise BoundaryError("INTEGRITY_ERROR", "bank의 해시가 ref와 다르다")
            if (qsid, n) in self._hashes:
                if self._hashes[(qsid, n)] == ref["content_hash"]:
                    return
                raise BoundaryError("CONFLICT", f"같은 버전에 다른 내용: {qsid}@{n}")
            rows = self._version_rows(qsid)
            expect = (rows[-1][0] + 1) if rows else 1
            if n != expect:
                raise BoundaryError("CONFLICT", f"다음 버전 번호는 {expect}이어야 한다: {n}")
            if review["review_id"] in self._review_ids:
                raise BoundaryError("CONFLICT", f"review_id가 이미 있다: {review['review_id']}")
            self._banks[(qsid, n)] = copy.deepcopy(bank)
            self._hashes[(qsid, n)] = ref["content_hash"]
            self._reviews[(qsid, n)] = [copy.deepcopy(review)]
            self._review_ids[review["review_id"]] = copy.deepcopy(review)
            if change is not None:
                self._changes[(qsid, n)] = copy.deepcopy(change)
            if source is not None:
                self._sources_prov[(qsid, n)] = copy.deepcopy(source)

    def add_review(self, review):
        ref = review["question_set"]
        qsid, n = ref["id"], int(ref["version"])
        with self._lock:
            if self._hashes.get((qsid, n)) != ref["content_hash"]:
                raise BoundaryError("NOT_FOUND", "검수 대상 버전이 없거나 해시가 다르다")
            old = self._review_ids.get(review["review_id"])
            if old is not None:
                if old == review:
                    return
                raise BoundaryError("CONFLICT", f"같은 review_id에 다른 내용: {review['review_id']}")
            self._reviews[(qsid, n)].append(copy.deepcopy(review))
            self._review_ids[review["review_id"]] = copy.deepcopy(review)

    def save_draft(self, draft):
        with self._lock:
            self._drafts[draft["draft_id"]] = copy.deepcopy(draft)

    def load_draft(self, draft_id):
        with self._lock:
            d = self._drafts.get(draft_id)
            return copy.deepcopy(d) if d is not None else None

    def list_drafts(self, question_set_id=None):
        with self._lock:
            return [copy.deepcopy(d) for d in self._drafts.values()
                    if question_set_id is None or d["question_set_id"] == question_set_id]

    def put_source(self, source_hash, filename, data):
        with self._lock:
            if source_hash not in self._blobs:
                self._blobs[source_hash] = (filename, bytes(data))
            self._blob_times[source_hash] = _now_ts()      # 같은 자료를 다시 올리면 보존 기간이 새로 센다

    def get_source(self, source_hash):
        with self._lock:
            return self._blobs.get(source_hash)

    def _delete_draft_if_unchanged(self, draft):
        with self._lock:
            if self._drafts.get(draft["draft_id"]) == draft:
                del self._drafts[draft["draft_id"]]
                return True
            return False

    def _version_source_hashes(self):
        with self._lock:
            return {v.get("source_hash") for v in self._sources_prov.values() if v.get("source_hash")}

    def _blob_meta(self):
        with self._lock:
            return [(h, self._blob_times.get(h)) for h in self._blobs]

    def _stamp_blobs(self, now):
        with self._lock:
            for h in self._blobs:
                if self._blob_times.get(h) is None:
                    self._blob_times[h] = now

    def _delete_blob_if_stale(self, source_hash, before):
        with self._lock:
            t = self._blob_times.get(source_hash)
            if source_hash not in self._blobs or t is None or not t < before:
                return False
            if source_hash in self._version_source_hashes() or any(
                    (d.get("source") or {}).get("source_hash") == source_hash for d in self._drafts.values()):
                return False
            del self._blobs[source_hash]
            self._blob_times.pop(source_hash, None)
            return True

    # 시험용: 저장된 내용을 일부러 망가뜨린다(무결성 시험). 운영 코드는 부르지 않는다.
    def _corrupt_for_test(self, qsid: str, n: int, bank: dict) -> None:
        self._banks[(qsid, n)] = copy.deepcopy(bank)
