"""PostgreSQL 구현(운영). SQLite 구현과 같은 인터페이스·같은 의미를 낸다.

- 버전·검수·변경 분류 테이블은 삽입만 허용한다(UPDATE/DELETE는 트리거가 막는다, D25).
- 쓰기는 트랜잭션 하나 안에서, 트랜잭션 수준 advisory lock으로 직렬화한다(SQLite의 BEGIN IMMEDIATE에 해당).
  그래서 "다음 버전 번호는 마지막 + 1" 검사와 삽입 사이에 다른 쓰기가 끼어들지 못한다(여러 프로세스 포함).
- 시작 시 스키마를 멱등하게 만든다(IF NOT EXISTS, 같은 lock 아래). 읽기 전용 연결은 만들지 않고 형식만 확인한다.
- 접속 문자열(암호 포함)은 로그·오류·예외 메시지에 쓰지 않는다. 오류에는 예외 종류와 SQLSTATE만 낸다.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Optional

import psycopg

from boundary_contracts.errors import BoundaryError
from .base import AuthoringStore, bank_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS versions(
  qsid TEXT NOT NULL, version_no INTEGER NOT NULL, content_hash TEXT NOT NULL, bank TEXT NOT NULL,
  source TEXT, PRIMARY KEY(qsid, version_no));
CREATE TABLE IF NOT EXISTS reviews(
  seq BIGSERIAL PRIMARY KEY, review_id TEXT NOT NULL UNIQUE, qsid TEXT NOT NULL,
  version_no INTEGER NOT NULL, record TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS reviews_ver ON reviews(qsid, version_no);
CREATE TABLE IF NOT EXISTS changes(
  qsid TEXT NOT NULL, version_no INTEGER NOT NULL, record TEXT NOT NULL, PRIMARY KEY(qsid, version_no));
CREATE TABLE IF NOT EXISTS drafts(
  seq BIGSERIAL, draft_id TEXT PRIMARY KEY, qsid TEXT NOT NULL, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS blobs(source_hash TEXT PRIMARY KEY, filename TEXT NOT NULL, data BYTEA NOT NULL);
CREATE OR REPLACE FUNCTION qa_immutable() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION '% 는 불변이다', TG_TABLE_NAME USING ERRCODE = 'integrity_constraint_violation'; END;
$$ LANGUAGE plpgsql;
"""
IMMUTABLE = ["versions", "reviews", "changes"]
SCHEMA_VERSION = "1"
_LOCK_KEY = 0x71A07E5  # 이 서비스의 쓰기 직렬화 키(데이터베이스 안에서만 의미가 있다)


def _dump(o) -> str:
    return json.dumps(o, ensure_ascii=False, sort_keys=True)


def _why(e: BaseException) -> str:
    """비밀이 섞일 수 있는 원문 메시지는 버리고 종류와 SQLSTATE만 남긴다."""
    return f"{type(e).__name__}" + (f"({e.sqlstate})" if getattr(e, "sqlstate", None) else "")


class PostgresQuestionStore(AuthoringStore):
    """url은 libpq 접속 문자열(URI). schema를 주면 그 스키마를 search_path로 쓴다(시험의 격리용; 기본은 서버 기본값).
    read_only=True면 세션을 읽기 전용으로 열고 스키마를 만들지 않는다(이웃 영역에 주는 읽기 계정에 해당)."""

    kind = "postgres"

    def __init__(self, url: str, *, read_only: bool = False, schema: Optional[str] = None):
        if schema is not None and not re.fullmatch(r"[a-z_][a-z0-9_]{0,40}", schema):
            raise ValueError("스키마 이름 형식이 아니다")
        self._lock = threading.RLock()
        self._url = url
        self._read_only = read_only
        self._schema = schema
        self._db: Optional[psycopg.Connection] = None
        with self._lock:
            self._conn()
            if not read_only:
                self._init_schema()
            row = self._q("SELECT v FROM meta WHERE k='schema_version'")
            if not row or row[0][0] != SCHEMA_VERSION:
                self.close()
                raise BoundaryError("UNAVAILABLE", "문항 저장소 DB 형식을 알 수 없다")

    # ---- 연결 ----
    def _conn(self) -> psycopg.Connection:
        if self._db is not None and not self._db.closed:
            return self._db
        opts = []
        if self._schema:
            opts.append(f"-c search_path={self._schema}")
        if self._read_only:
            opts.append("-c default_transaction_read_only=on")
        c, err = None, None
        try:
            c = psycopg.connect(self._url, autocommit=True, connect_timeout=5,
                                **({"options": " ".join(opts)} if opts else {}))
        except psycopg.OperationalError as e:
            err = f"저장소 연결 실패: {_why(e)}"
        except Exception as e:  # 접속 문자열 형식 오류 등. 예외 원문·문맥에 접속 문자열이 있을 수 있어 아래에서 문맥 없이 올린다
            err = f"저장소 접속 설정이 올바르지 않다: {type(e).__name__}"
        if err:
            raise BoundaryError("UNAVAILABLE", err)  # except 밖에서 올려 __context__에 원 예외(접속 문자열)가 남지 않는다
        self._db = c
        return c

    def _init_schema(self) -> None:
        c = self._conn()
        try:
            with c.transaction():
                c.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
                c.execute(SCHEMA)
                for t in IMMUTABLE:
                    c.execute(f"DROP TRIGGER IF EXISTS {t}_immutable ON {t}")
                    c.execute(f"CREATE TRIGGER {t}_immutable BEFORE UPDATE OR DELETE ON {t} "
                              f"FOR EACH ROW EXECUTE FUNCTION qa_immutable()")
                c.execute("INSERT INTO meta(k, v) VALUES('schema_version', %s) ON CONFLICT DO NOTHING", (SCHEMA_VERSION,))
        except psycopg.Error as e:
            raise BoundaryError("UNAVAILABLE", f"저장소 스키마 준비 실패: {_why(e)}") from None

    def close(self):
        with self._lock:
            if self._db is not None:
                try:
                    self._db.close()
                finally:
                    self._db = None

    def _drop_if_broken(self, e: psycopg.Error) -> None:
        if isinstance(e, psycopg.OperationalError) and self._db is not None:
            try:
                self._db.close()
            finally:
                self._db = None

    def _q(self, sql, args=()):
        with self._lock:
            try:
                return self._conn().execute(sql, args).fetchall()
            except psycopg.Error as e:
                self._drop_if_broken(e)
                raise BoundaryError("UNAVAILABLE", f"저장소 읽기 실패: {_why(e)}") from None

    def _write(self, fn):
        if self._read_only:
            raise BoundaryError("PERMISSION_DENIED", "읽기 전용 연결이다")
        with self._lock:
            c = self._conn()
            try:
                with c.transaction():
                    c.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
                    return fn(c)
            except psycopg.Error as e:
                self._drop_if_broken(e)
                raise BoundaryError("UNAVAILABLE", f"저장소 쓰기 실패: {_why(e)}") from None

    # ---- 원시 연산 ----
    def _version_rows(self, qsid):
        return [(r[0], r[1]) for r in self._q(
            "SELECT version_no, content_hash FROM versions WHERE qsid=%s ORDER BY version_no", (qsid,))]

    def _ids(self):
        return sorted(r[0] for r in self._q("SELECT DISTINCT qsid FROM versions"))  # 파이썬 순서(코드포인트)로 정렬

    def _bank_json(self, qsid, n):
        r = self._q("SELECT bank FROM versions WHERE qsid=%s AND version_no=%s", (qsid, n))
        return json.loads(r[0][0]) if r else None

    def _review_rows(self, qsid, n):
        return [json.loads(r[0]) for r in self._q(
            "SELECT record FROM reviews WHERE qsid=%s AND version_no=%s ORDER BY seq", (qsid, n))]

    def _change_row(self, qsid, n):
        r = self._q("SELECT record FROM changes WHERE qsid=%s AND version_no=%s", (qsid, n))
        return json.loads(r[0][0]) if r else None

    def commit_version(self, bank, ref, review, change, source):
        qsid, n = ref["id"], int(ref["version"])
        if bank_hash(bank) != ref["content_hash"]:
            raise BoundaryError("INTEGRITY_ERROR", "bank의 해시가 ref와 다르다")

        def fn(c):
            ex = c.execute("SELECT content_hash FROM versions WHERE qsid=%s AND version_no=%s", (qsid, n)).fetchone()
            if ex:
                if ex[0] == ref["content_hash"]:
                    return
                raise BoundaryError("CONFLICT", f"같은 버전에 다른 내용: {qsid}@{n}")
            last = c.execute("SELECT MAX(version_no) FROM versions WHERE qsid=%s", (qsid,)).fetchone()[0] or 0
            if n != last + 1:
                raise BoundaryError("CONFLICT", f"다음 버전 번호는 {last + 1}이어야 한다: {n}")
            if c.execute("SELECT 1 FROM reviews WHERE review_id=%s", (review["review_id"],)).fetchone():
                raise BoundaryError("CONFLICT", f"review_id가 이미 있다: {review['review_id']}")
            c.execute("INSERT INTO versions(qsid, version_no, content_hash, bank, source) VALUES(%s,%s,%s,%s,%s)",
                      (qsid, n, ref["content_hash"], _dump(bank), _dump(source) if source else None))
            c.execute("INSERT INTO reviews(review_id, qsid, version_no, record) VALUES(%s,%s,%s,%s)",
                      (review["review_id"], qsid, n, _dump(review)))
            if change is not None:
                c.execute("INSERT INTO changes(qsid, version_no, record) VALUES(%s,%s,%s)", (qsid, n, _dump(change)))
        self._write(fn)

    def add_review(self, review):
        ref = review["question_set"]
        qsid, n = ref["id"], int(ref["version"])

        def fn(c):
            ver = c.execute("SELECT content_hash FROM versions WHERE qsid=%s AND version_no=%s", (qsid, n)).fetchone()
            if not ver or ver[0] != ref["content_hash"]:
                raise BoundaryError("NOT_FOUND", "검수 대상 버전이 없거나 해시가 다르다")
            old = c.execute("SELECT record FROM reviews WHERE review_id=%s", (review["review_id"],)).fetchone()
            if old:
                if json.loads(old[0]) == review:
                    return
                raise BoundaryError("CONFLICT", f"같은 review_id에 다른 내용: {review['review_id']}")
            c.execute("INSERT INTO reviews(review_id, qsid, version_no, record) VALUES(%s,%s,%s,%s)",
                      (review["review_id"], qsid, n, _dump(review)))
        self._write(fn)

    def save_draft(self, draft):
        def fn(c):  # SQLite의 INSERT OR REPLACE와 같다: 새 순번을 받는다
            c.execute("DELETE FROM drafts WHERE draft_id=%s", (draft["draft_id"],))
            c.execute("INSERT INTO drafts(draft_id, qsid, record) VALUES(%s,%s,%s)",
                      (draft["draft_id"], draft["question_set_id"], _dump(draft)))
        self._write(fn)

    def load_draft(self, draft_id):
        r = self._q("SELECT record FROM drafts WHERE draft_id=%s", (draft_id,))
        return json.loads(r[0][0]) if r else None

    def list_drafts(self, question_set_id=None):
        rows = self._q("SELECT record FROM drafts ORDER BY seq") if question_set_id is None else \
            self._q("SELECT record FROM drafts WHERE qsid=%s ORDER BY seq", (question_set_id,))
        return [json.loads(r[0]) for r in rows]

    def put_source(self, source_hash, filename, data):
        self._write(lambda c: c.execute(
            "INSERT INTO blobs(source_hash, filename, data) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
            (source_hash, filename, bytes(data))))

    def get_source(self, source_hash):
        r = self._q("SELECT filename, data FROM blobs WHERE source_hash=%s", (source_hash,))
        return (r[0][0], bytes(r[0][1])) if r else None

    def _corrupt_for_test(self, qsid: str, n: int, bank: dict) -> None:
        """시험 전용: 불변 트리거를 잠시 떼고 내용을 바꿔 무결성 검사가 잡는지 본다."""
        with self._lock, self._conn().transaction():
            c = self._conn()
            c.execute("ALTER TABLE versions DISABLE TRIGGER versions_immutable")
            c.execute("UPDATE versions SET bank=%s WHERE qsid=%s AND version_no=%s", (_dump(bank), qsid, n))
            c.execute("ALTER TABLE versions ENABLE TRIGGER versions_immutable")

    def is_ready(self) -> bool:
        try:
            self._q("SELECT 1")
            return True
        except BoundaryError:
            return False
