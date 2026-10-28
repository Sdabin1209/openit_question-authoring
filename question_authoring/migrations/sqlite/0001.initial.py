"""최초 스키마(SQLite). 이미 같은 테이블이 있는 DB(마이그레이션 도입 이전에 만든 것)도 받아들이도록 모두 IF NOT EXISTS다. ADR 0017"""
from yoyo import step

__transactional__ = False   # SQLite DDL 트리거 생성은 yoyo의 트랜잭션 래핑 없이 문장 단위로 적용한다

step("CREATE TABLE IF NOT EXISTS versions(qsid TEXT NOT NULL, version_no INTEGER NOT NULL, content_hash TEXT NOT NULL, "
     "bank TEXT NOT NULL, source TEXT, PRIMARY KEY(qsid, version_no))")
step("CREATE TABLE IF NOT EXISTS reviews(seq INTEGER PRIMARY KEY AUTOINCREMENT, review_id TEXT NOT NULL UNIQUE, "
     "qsid TEXT NOT NULL, version_no INTEGER NOT NULL, record TEXT NOT NULL)")
step("CREATE INDEX IF NOT EXISTS reviews_ver ON reviews(qsid, version_no)")
step("CREATE TABLE IF NOT EXISTS changes(qsid TEXT NOT NULL, version_no INTEGER NOT NULL, record TEXT NOT NULL, "
     "PRIMARY KEY(qsid, version_no))")
step("CREATE TABLE IF NOT EXISTS drafts(draft_id TEXT PRIMARY KEY, qsid TEXT NOT NULL, record TEXT NOT NULL)")
step("CREATE TABLE IF NOT EXISTS blobs(source_hash TEXT PRIMARY KEY, filename TEXT NOT NULL, data BLOB NOT NULL)")
# 버전·검수·변경 분류는 삽입만 허용한다(D25)
for t in ("versions", "reviews", "changes"):
    for op in ("UPDATE", "DELETE"):
        step(f"CREATE TRIGGER IF NOT EXISTS {t}_no_{op.lower()} BEFORE {op} ON {t} "
             f"BEGIN SELECT RAISE(ABORT, '{t}는 불변이다'); END")
