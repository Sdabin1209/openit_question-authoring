"""원본 자료에 보존 기간 기준 시각(created_at) 열 추가(D88 U4). 이미 있는 DB(그 이후에 만든 것)는 건너뛴다. SQLite에는 ADD COLUMN IF NOT EXISTS가 없다."""
from yoyo import step

__depends__ = {"0001.initial"}
__transactional__ = False


def add_created_at(conn):
    cur = conn.cursor()
    cols = [r[1] for r in cur.execute("PRAGMA table_info(blobs)").fetchall()]
    if "created_at" not in cols:
        cur.execute("ALTER TABLE blobs ADD COLUMN created_at TEXT")


step(add_created_at)
