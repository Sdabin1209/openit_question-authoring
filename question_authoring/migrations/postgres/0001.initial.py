"""최초 스키마(PostgreSQL). 이미 같은 테이블이 있는 DB(마이그레이션 도입 이전에 만든 것)도 받아들이도록 IF NOT EXISTS / OR REPLACE / DROP IF EXISTS다. ADR 0017"""
from yoyo import step

step("CREATE TABLE IF NOT EXISTS versions(qsid TEXT NOT NULL, version_no INTEGER NOT NULL, content_hash TEXT NOT NULL, "
     "bank TEXT NOT NULL, source TEXT, PRIMARY KEY(qsid, version_no))")
step("CREATE TABLE IF NOT EXISTS reviews(seq BIGSERIAL PRIMARY KEY, review_id TEXT NOT NULL UNIQUE, qsid TEXT NOT NULL, "
     "version_no INTEGER NOT NULL, record TEXT NOT NULL)")
step("CREATE INDEX IF NOT EXISTS reviews_ver ON reviews(qsid, version_no)")
step("CREATE TABLE IF NOT EXISTS changes(qsid TEXT NOT NULL, version_no INTEGER NOT NULL, record TEXT NOT NULL, "
     "PRIMARY KEY(qsid, version_no))")
step("CREATE TABLE IF NOT EXISTS drafts(seq BIGSERIAL, draft_id TEXT PRIMARY KEY, qsid TEXT NOT NULL, record TEXT NOT NULL)")
step("CREATE TABLE IF NOT EXISTS blobs(source_hash TEXT PRIMARY KEY, filename TEXT NOT NULL, data BYTEA NOT NULL)")
step("CREATE OR REPLACE FUNCTION qa_immutable() RETURNS trigger AS $$ "
     "BEGIN RAISE EXCEPTION '% 는 불변이다', TG_TABLE_NAME USING ERRCODE = 'integrity_constraint_violation'; END; "
     "$$ LANGUAGE plpgsql")
for t in ("versions", "reviews", "changes"):
    step(f"DROP TRIGGER IF EXISTS {t}_immutable ON {t}")
    step(f"CREATE TRIGGER {t}_immutable BEFORE UPDATE OR DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION qa_immutable()")
