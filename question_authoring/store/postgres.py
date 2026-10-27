"""PostgreSQL 구현(운영). SQLite 구현과 같은 인터페이스·같은 의미를 낸다.

- 버전·검수·변경 분류 테이블은 삽입만 허용한다(UPDATE/DELETE는 트리거가 막는다, D25).
- 쓰기는 트랜잭션 하나 안에서, 트랜잭션 수준 advisory lock으로 직렬화한다(SQLite의 BEGIN IMMEDIATE에 해당).
  그래서 "다음 버전 번호는 마지막 + 1" 검사와 삽입 사이에 다른 쓰기가 끼어들지 못한다(여러 프로세스 포함).
- 시작 시 스키마를 멱등하게 만든다(IF NOT EXISTS, 같은 lock 아래). 읽기 전용 연결은 만들지 않고 형식만 확인한다.
- 접속 문자열(암호 포함)은 로그·오류·예외 메시지에 쓰지 않는다. 오류에는 예외 종류와 SQLSTATE만 낸다.
- 대기 상한(docs/boundary.md "DB 대기 상한"): 접속은 connect_timeout, 문장은 서버 쪽 statement_timeout. 서버가 얼어 응답이 없으면
  서버 쪽 값은 소용없으므로 호출 하나가 statement_timeout + 1초 안에 안 끝나면 클라이언트가 소켓을 닫아 끝낸다(UNAVAILABLE).
  /readyz의 확인(is_ready)은 주 연결·락을 쓰지 않는 별도 짧은 연결로 probe_timeout 안에 끝난다.
"""
from __future__ import annotations

import json
import os
import re
import socket
import threading
from typing import Optional

import psycopg
import psycopg.conninfo

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
# 기본 상한(초). 근거는 docs/boundary.md "DB 대기 상한".
DEFAULT_CONNECT_TIMEOUT = 3.0
DEFAULT_STATEMENT_TIMEOUT = 3.0
DEFAULT_PROBE_TIMEOUT = 2.0
_INIT_STATEMENT_MS = 30000   # 시작 때 스키마 생성은 다른 인스턴스의 advisory lock을 기다릴 수 있어 더 길게 준다(그래도 무한은 아니다)
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

    def __init__(self, url: str, *, read_only: bool = False, schema: Optional[str] = None,
                 connect_timeout: float = DEFAULT_CONNECT_TIMEOUT, statement_timeout: float = DEFAULT_STATEMENT_TIMEOUT,
                 probe_timeout: float = DEFAULT_PROBE_TIMEOUT):
        for name, v in (("connect_timeout", connect_timeout), ("statement_timeout", statement_timeout), ("probe_timeout", probe_timeout)):
            if not (isinstance(v, (int, float)) and 0 < v <= 600):
                raise ValueError(f"{name}은 0보다 크고 600 이하인 초 단위 숫자여야 한다")
        self.connect_timeout = float(connect_timeout)        # 접속 시도 상한(초). libpq는 정수 초이고 2초 미만은 2초로 본다
        self.statement_timeout = float(statement_timeout)    # 문장 하나의 상한(초). 호출 하나의 클라이언트 쪽 상한은 이 값 + 1초
        self.probe_timeout = float(probe_timeout)            # /readyz의 DB 확인 상한(초)
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
        c, err = None, None
        try:
            c = psycopg.connect(autocommit=True, **self._params(self.connect_timeout, self.statement_timeout))
        except psycopg.OperationalError as e:
            err = f"저장소 연결 실패: {_why(e)}"
        except Exception as e:  # 접속 문자열 형식 오류 등. 예외 원문·문맥에 접속 문자열이 있을 수 있어 아래에서 문맥 없이 올린다
            err = f"저장소 접속 설정이 올바르지 않다: {type(e).__name__}"
        if err:
            raise BoundaryError("UNAVAILABLE", err)  # except 밖에서 올려 __context__에 원 예외(접속 문자열)가 남지 않는다
        self._db = c
        return c

    def _params(self, connect_secs: float, stmt_secs: float) -> dict:
        """접속 인자. URL 안의 options는 덮어쓰지 않고 이어 붙인다. 서버 쪽 statement_timeout을 세션에 건다."""
        p = psycopg.conninfo.conninfo_to_dict(self._url)
        opts = [p.get("options", "")]
        if self._schema:
            opts.append(f"-c search_path={self._schema}")
        if self._read_only:
            opts.append("-c default_transaction_read_only=on")
        opts.append(f"-c statement_timeout={int(stmt_secs * 1000)}")
        p["options"] = " ".join(o for o in opts if o)
        p["connect_timeout"] = max(1, int(connect_secs + 0.999))
        return p

    @staticmethod
    def _abort(conn) -> None:
        """서버가 얼어 응답이 없을 때(예: docker pause) 서버 쪽 statement_timeout은 듣지 못한다. 소켓을 닫아 막힌 읽기를 끝낸다."""
        try:
            sk = socket.socket(fileno=os.dup(conn.fileno()))
            try:
                sk.shutdown(socket.SHUT_RDWR)
            finally:
                sk.close()
        except Exception:
            pass

    def _init_schema(self) -> None:
        c = self._conn()
        try:
            with c.transaction():
                c.execute(f"SET LOCAL statement_timeout = {_INIT_STATEMENT_MS}")
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

    @staticmethod
    def _lost(e: BaseException) -> bool:
        """연결 끊김만 가려낸다: 클라이언트 쪽 끊김(SQLSTATE 없음), 08류(연결 예외), 57P01~03(서버 종료·크래시·재시작).
        제약 위반·문법 오류·취소·잠금 시간 초과 같은 오류는 재시도하지 않는다."""
        if not isinstance(e, psycopg.OperationalError):
            return False
        st = getattr(e, "sqlstate", None)
        return st is None or st.startswith("08") or st in ("57P01", "57P02", "57P03")

    def _drop(self) -> None:
        if self._db is not None:
            try:
                self._db.close()
            except Exception:
                pass
            self._db = None

    def _run(self, op, what: str):
        """op(conn)을 실행한다. 연결이 끊겼으면 연결을 버리고 다시 열어 한 번만 같은 호출을 재시도한다.
        재시도가 안전한 근거: 쓰기는 모두 한 트랜잭션이고(커밋 전 끊기면 서버가 롤백) 커밋 응답만 잃은 경우에도
        모든 쓰기 연산이 멱등이다(같은 버전·같은 해시, 같은 review_id·같은 내용은 조용히 성공, 초안은 삭제 후 삽입,
        원본 자료는 ON CONFLICT DO NOTHING). 재시도는 같은 advisory lock 트랜잭션을 처음부터 다시 연다.
        두 번째도 실패하면 UNAVAILABLE."""
        with self._lock:
            err = None
            for attempt in (1, 2):
                conn = None
                timer = None
                aborted = threading.Event()
                try:
                    conn = self._conn()

                    def fire(c=conn):
                        aborted.set()
                        self._abort(c)
                    timer = threading.Timer(self.statement_timeout + 1.0, fire)
                    timer.daemon = True
                    timer.start()
                    return op(conn)
                except psycopg.Error as e:
                    if aborted.is_set():   # 응답 없는 서버를 클라이언트가 끊었다. 같은 서버에 바로 다시 걸지 않는다(상한을 두 배로 늘리지 않는다)
                        self._drop()
                        err = f"저장소 {what} 시간 초과: {_why(e)}"
                        break
                    if self._lost(e) or (self._db is not None and (self._db.closed or self._db.broken)):
                        self._drop()
                        if attempt == 1:
                            continue
                    err = f"저장소 {what} 실패: {_why(e)}"
                    break
                finally:
                    if timer is not None:
                        timer.cancel()
            raise BoundaryError("UNAVAILABLE", err)  # except 밖에서 올린다(문맥에 접속 정보가 남지 않게)

    def _q(self, sql, args=()):
        return self._run(lambda c: c.execute(sql, args).fetchall(), "읽기")

    def _write(self, fn):
        if self._read_only:
            raise BoundaryError("PERMISSION_DENIED", "읽기 전용 연결이다")

        def op(c):
            with c.transaction():
                c.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
                return fn(c)
        return self._run(op, "쓰기")

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
        """/readyz용 DB 확인. **주 연결도 self._lock도 쓰지 않는다**: 쓰기가 연결을 쥐고 기다리는 중이어도 막히지 않는다.
        별도 짧은 연결로 접속하고(connect_timeout·statement_timeout = probe_timeout) 형식 표지를 읽는다.
        접속 뒤 서버가 얼어도 probe_timeout 안에 호출자가 돌아온다(작업 스레드를 기다리다 포기하고 그 소켓을 닫는다).
        실패 원인은 돌려주지 않는다(접속 정보가 섞일 수 있다)."""
        done = threading.Event()
        box: dict = {}

        def work():
            try:
                with psycopg.connect(autocommit=True, **self._params(self.probe_timeout, self.probe_timeout)) as c:
                    box["conn"] = c
                    if box.get("giveup"):
                        return
                    row = c.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
                    box["ok"] = bool(row) and row[0] == SCHEMA_VERSION
            except Exception:
                box["ok"] = False
            finally:
                done.set()

        threading.Thread(target=work, daemon=True, name="qa-readyz-probe").start()
        if not done.wait(self.probe_timeout):
            box["giveup"] = True
            if box.get("conn") is not None:
                self._abort(box["conn"])      # 얼어 있는 소켓을 닫아 작업 스레드도 끝나게 한다
            return False
        return bool(box.get("ok"))
