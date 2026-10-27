"""SQLite 구현. 버전·검수·변경 분류 테이블은 삽입만 허용한다(UPDATE/DELETE는 트리거가 막는다, D25)."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from boundary_contracts.errors import BoundaryError
from .base import AuthoringStore, bank_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS versions(
  qsid TEXT NOT NULL, version_no INTEGER NOT NULL, content_hash TEXT NOT NULL, bank TEXT NOT NULL,
  source TEXT, PRIMARY KEY(qsid, version_no));
CREATE TABLE IF NOT EXISTS reviews(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, review_id TEXT NOT NULL UNIQUE, qsid TEXT NOT NULL,
  version_no INTEGER NOT NULL, record TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS reviews_ver ON reviews(qsid, version_no);
CREATE TABLE IF NOT EXISTS changes(
  qsid TEXT NOT NULL, version_no INTEGER NOT NULL, record TEXT NOT NULL, PRIMARY KEY(qsid, version_no));
CREATE TABLE IF NOT EXISTS drafts(draft_id TEXT PRIMARY KEY, qsid TEXT NOT NULL, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS blobs(source_hash TEXT PRIMARY KEY, filename TEXT NOT NULL, data BLOB NOT NULL, created_at TEXT);
"""
IMMUTABLE = ["versions", "reviews", "changes"]
SCHEMA_VERSION = "1"


def _triggers() -> str:
    out = []
    for t in IMMUTABLE:
        for op in ("UPDATE", "DELETE"):
            out.append(f"CREATE TRIGGER IF NOT EXISTS {t}_no_{op.lower()} BEFORE {op} ON {t} "
                       f"BEGIN SELECT RAISE(ABORT, '{t}는 불변이다'); END;")
    return "\n".join(out)


def _now_ts() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dump(o) -> str:
    return json.dumps(o, ensure_ascii=False, sort_keys=True)


class SqliteQuestionStore(AuthoringStore):
    """path가 None이면 ':memory:'가 아니라 임시 파일 없이 메모리 DB(시험용)를 쓴다.
    read_only=True면 읽기 전용으로 연다(이웃 영역에 주는 읽기 계정에 해당)."""

    kind = "sqlite"

    def __init__(self, path: str | Path | None = None, *, read_only: bool = False):
        self._lock = threading.RLock()
        self._read_only = read_only
        if path is None:
            self._db = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        elif read_only:
            self._db = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True,
                                       check_same_thread=False, isolation_level=None)
        else:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA foreign_keys=ON")
        if not read_only:
            self._db.execute("PRAGMA journal_mode=WAL") if path is not None else None
            self._db.executescript(SCHEMA + _triggers())
            if "created_at" not in [r[1] for r in self._db.execute("PRAGMA table_info(blobs)").fetchall()]:
                self._db.execute("ALTER TABLE blobs ADD COLUMN created_at TEXT")   # 보존 기간(D88 U4) 이전에 만든 DB
            self._db.execute("INSERT OR IGNORE INTO meta(k, v) VALUES('schema_version', ?)", (SCHEMA_VERSION,))
        row = self._db.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
        if row is None or row[0] != SCHEMA_VERSION:
            raise BoundaryError("UNAVAILABLE", "문항 저장소 DB 형식을 알 수 없다")

    def close(self):
        with self._lock:
            self._db.close()

    def _q(self, sql, args=()):
        with self._lock:
            try:
                return self._db.execute(sql, args).fetchall()
            except sqlite3.Error as e:
                raise BoundaryError("UNAVAILABLE", f"저장소 읽기 실패: {e}") from e

    def _version_rows(self, qsid):
        return [(r[0], r[1]) for r in self._q(
            "SELECT version_no, content_hash FROM versions WHERE qsid=? ORDER BY version_no", (qsid,))]

    def _ids(self):
        return [r[0] for r in self._q("SELECT DISTINCT qsid FROM versions ORDER BY qsid")]

    def _bank_json(self, qsid, n):
        r = self._q("SELECT bank FROM versions WHERE qsid=? AND version_no=?", (qsid, n))
        return json.loads(r[0][0]) if r else None

    def _review_rows(self, qsid, n):
        return [json.loads(r[0]) for r in self._q(
            "SELECT record FROM reviews WHERE qsid=? AND version_no=? ORDER BY seq", (qsid, n))]

    def _change_row(self, qsid, n):
        r = self._q("SELECT record FROM changes WHERE qsid=? AND version_no=?", (qsid, n))
        return json.loads(r[0][0]) if r else None

    def _write(self, fn):
        if self._read_only:
            raise BoundaryError("PERMISSION_DENIED", "읽기 전용 연결이다")
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    res = fn()
                    self._db.execute("COMMIT")
                    return res
                except BaseException:
                    self._db.execute("ROLLBACK")
                    raise
            except sqlite3.Error as e:
                raise BoundaryError("UNAVAILABLE", f"저장소 쓰기 실패: {e}") from e

    def commit_version(self, bank, ref, review, change, source):
        qsid, n = ref["id"], int(ref["version"])
        if bank_hash(bank) != ref["content_hash"]:
            raise BoundaryError("INTEGRITY_ERROR", "bank의 해시가 ref와 다르다")

        def fn():
            ex = self._db.execute("SELECT content_hash FROM versions WHERE qsid=? AND version_no=?", (qsid, n)).fetchone()
            if ex:
                if ex[0] == ref["content_hash"]:
                    return
                raise BoundaryError("CONFLICT", f"같은 버전에 다른 내용: {qsid}@{n}")
            last = self._db.execute("SELECT MAX(version_no) FROM versions WHERE qsid=?", (qsid,)).fetchone()[0] or 0
            if n != last + 1:
                raise BoundaryError("CONFLICT", f"다음 버전 번호는 {last + 1}이어야 한다: {n}")
            if self._db.execute("SELECT 1 FROM reviews WHERE review_id=?", (review["review_id"],)).fetchone():
                raise BoundaryError("CONFLICT", f"review_id가 이미 있다: {review['review_id']}")
            self._db.execute("INSERT INTO versions(qsid, version_no, content_hash, bank, source) VALUES(?,?,?,?,?)",
                             (qsid, n, ref["content_hash"], _dump(bank), _dump(source) if source else None))
            self._db.execute("INSERT INTO reviews(review_id, qsid, version_no, record) VALUES(?,?,?,?)",
                             (review["review_id"], qsid, n, _dump(review)))
            if change is not None:
                self._db.execute("INSERT INTO changes(qsid, version_no, record) VALUES(?,?,?)", (qsid, n, _dump(change)))
        self._write(fn)

    def add_review(self, review):
        ref = review["question_set"]
        qsid, n = ref["id"], int(ref["version"])

        def fn():
            ver = self._db.execute("SELECT content_hash FROM versions WHERE qsid=? AND version_no=?", (qsid, n)).fetchone()
            if not ver or ver[0] != ref["content_hash"]:
                raise BoundaryError("NOT_FOUND", "검수 대상 버전이 없거나 해시가 다르다")
            old = self._db.execute("SELECT record FROM reviews WHERE review_id=?", (review["review_id"],)).fetchone()
            if old:
                if json.loads(old[0]) == review:
                    return
                raise BoundaryError("CONFLICT", f"같은 review_id에 다른 내용: {review['review_id']}")
            self._db.execute("INSERT INTO reviews(review_id, qsid, version_no, record) VALUES(?,?,?,?)",
                             (review["review_id"], qsid, n, _dump(review)))
        self._write(fn)

    def save_draft(self, draft):
        self._write(lambda: self._db.execute(
            "INSERT OR REPLACE INTO drafts(draft_id, qsid, record) VALUES(?,?,?)",
            (draft["draft_id"], draft["question_set_id"], _dump(draft))))

    def load_draft(self, draft_id):
        r = self._q("SELECT record FROM drafts WHERE draft_id=?", (draft_id,))
        return json.loads(r[0][0]) if r else None

    def list_drafts(self, question_set_id=None):
        rows = self._q("SELECT record FROM drafts ORDER BY rowid") if question_set_id is None else \
            self._q("SELECT record FROM drafts WHERE qsid=? ORDER BY rowid", (question_set_id,))
        return [json.loads(r[0]) for r in rows]

    def put_source(self, source_hash, filename, data):
        self._write(lambda: self._db.execute(
            "INSERT OR IGNORE INTO blobs(source_hash, filename, data, created_at) VALUES(?,?,?,?)",
            (source_hash, filename, bytes(data), _now_ts())))

    def get_source(self, source_hash):
        r = self._q("SELECT filename, data FROM blobs WHERE source_hash=?", (source_hash,))
        return (r[0][0], bytes(r[0][1])) if r else None

    def _delete_draft(self, draft_id):
        self._write(lambda: self._db.execute("DELETE FROM drafts WHERE draft_id=?", (draft_id,)))

    def _version_source_hashes(self):
        out = set()
        for (src,) in self._q("SELECT source FROM versions WHERE source IS NOT NULL"):
            h = (json.loads(src) or {}).get("source_hash")
            if h:
                out.add(h)
        return out

    def _blob_meta(self):
        return [(r[0], r[1]) for r in self._q("SELECT source_hash, created_at FROM blobs")]

    def _stamp_blobs(self, now):
        self._write(lambda: self._db.execute("UPDATE blobs SET created_at=? WHERE created_at IS NULL", (now,)))

    def _delete_blob(self, source_hash):
        self._write(lambda: self._db.execute("DELETE FROM blobs WHERE source_hash=?", (source_hash,)))

    def is_ready(self) -> bool:
        try:
            self._q("SELECT 1")
            return True
        except BoundaryError:
            return False
