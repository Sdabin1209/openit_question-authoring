"""스키마 마이그레이션(yoyo-migrations, SQLite·PostgreSQL 공통). ADR 0017.

시작 때 `migrate_*`가 아직 적용 안 된 마이그레이션을 순서대로 적용한다. 같은 DB에 여러 인스턴스가 동시에 시작해도
yoyo의 잠금 테이블이 직렬화한다. 읽기 전용 연결은 DB를 바꾸지 못하므로 `unapplied`로 적용 여부만 본다.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from yoyo import get_backend, read_migrations

_DIR = Path(__file__).resolve().parent.parent / "migrations"
TABLE = "qa_migration"   # 왜 따로: yoyo 기본 이름은 같은 DB를 쓰는 다른 도구와 겹칠 수 있다


def _migrations(dialect: str):
    return read_migrations(str(_DIR / dialect))


def _apply(uri: str, dialect: str, lock_seconds: float) -> None:
    backend = get_backend(uri, migration_table=TABLE)
    try:
        with backend.lock(timeout=int(lock_seconds)):
            backend.apply_migrations(backend.to_apply(_migrations(dialect)))
    finally:
        backend.connection.close()


def migrate_sqlite(path: str, lock_seconds: float = 30.0) -> None:
    _apply(f"sqlite:///{path}", "sqlite", lock_seconds)


def migrate_postgres(params: dict, lock_seconds: float = 30.0) -> None:
    """params: psycopg 접속 인자(dbname, user, password, host, port, options, connect_timeout, schema). 비밀이 URI에 들어가지만 로그·오류에 쓰지 않는다."""
    p = dict(params)
    auth = quote(str(p.pop("user", "")), safe="")
    if "password" in p:
        auth += ":" + quote(str(p.pop("password")), safe="")
    host = quote(str(p.pop("host", "")), safe="")
    port = f":{p.pop('port')}" if "port" in p else ""
    db = quote(str(p.pop("dbname", "")), safe="")
    query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in p.items())
    uri = f"postgresql+psycopg://{auth + '@' if auth else ''}{host}{port}/{db}" + (f"?{query}" if query else "")
    _apply(uri, "postgres", lock_seconds)


def known_hashes(dialect: str) -> set:
    return {m.hash for m in _migrations(dialect)}


def schema_matches(dialect: str, applied_hashes) -> bool:
    """DB에 적용된 마이그레이션이 이 코드가 아는 것과 정확히 같은가. 모르는 것이 더 적용됐으면(새 버전이 만든 DB) 거부한다 - 그 스키마를 이 코드가 읽는다고 보증할 수 없다."""
    return set(applied_hashes) == known_hashes(dialect)
