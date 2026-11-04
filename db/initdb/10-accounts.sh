#!/bin/bash
# 첫 기동 때 한 번(공식 postgres 이미지의 /docker-entrypoint-initdb.d, OS 사용자 postgres). 선언(/etc/openit/database.json = 라벨 io.openit.database)대로 계정·데이터베이스·권한을 만든다.
# 계정마다 LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION. owner 는 자기 데이터베이스를 소유하고, dml 은 CONNECT·USAGE·SELECT/INSERT/UPDATE/DELETE·시퀀스 USAGE/SELECT 와 기본 권한만 갖는다.
# 모든 데이터베이스에서 PUBLIC 의 CONNECT 를 회수한다. 암호는 DB_ACCOUNT_SECRETS_DIR/<계정 이름> 파일에서만 읽는다(D84): 값은 어떤 출력에도 쓰지 않는다.
set -euo pipefail
DECL=/etc/openit/database.json   # 고정 경로(이미지에 구워진 선언 = 라벨 io.openit.database). 덮어쓰는 환경 변수는 두지 않는다: AreaDatabaseEnv 에 없다
SECRETS="${DB_ACCOUNT_SECRETS_DIR:-/run/secrets/db}"
# 연결 상한(역할별): owner 40(서비스 풀과 마이그레이션), dml 8(정리·백업·읽기). max_connections 50 안. 측정하지 않은 시작값(ADR 0029)
LIMIT_OWNER=40; LIMIT_DML=8
psql_su() { psql -X -v ON_ERROR_STOP=1 -U postgres -d "${1:-postgres}" "${@:2}"; }

while IFS=$'\t' read -r name database privilege; do
  [ -s "$SECRETS/$name" ] || { echo "question-authoring-db: 계정 $name 의 암호 파일이 없거나 비어 있다" >&2; exit 1; }
  pw="$(head -n1 "$SECRETS/$name")"
  limit=$LIMIT_DML; [ "$privilege" = owner ] && limit=$LIMIT_OWNER
  psql_su postgres -v n="$name" -v pw="$pw" -v lim="$limit" <<'SQL' >/dev/null
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT %s PASSWORD %L', :'n', :'lim', :'pw') \gexec
SQL
done < <(jq -r '.accounts[] | [.name, .database, .privilege] | @tsv' "$DECL")

for db in $(jq -r '[.accounts[].database] | unique[]' "$DECL"); do
  owners="$(jq -r --arg d "$db" '[.accounts[] | select(.database==$d and .privilege=="owner") | .name] | .[]' "$DECL")"
  [ "$(printf '%s\n' "$owners" | grep -c .)" = 1 ] || { echo "question-authoring-db: 데이터베이스 $db 에는 owner 계정이 정확히 하나여야 한다" >&2; exit 1; }
  psql_su postgres -v d="$db" -v o="$owners" >/dev/null <<'SQL'
SELECT format('CREATE DATABASE %I OWNER %I', :'d', :'o') \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'d') \gexec
SQL
  # 이 데이터베이스에 접속하는 계정마다 CONNECT, dml 은 스키마·표·시퀀스 권한과 기본 권한
  for acct in $(jq -r --arg d "$db" '.accounts[] | select(.database==$d) | .name' "$DECL"); do
    priv="$(jq -r --arg a "$acct" '.accounts[] | select(.name==$a) | .privilege' "$DECL")"
    psql_su postgres -v d="$db" -v a="$acct" >/dev/null <<'SQL'
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'d', :'a') \gexec
SQL
    if [ "$priv" = dml ]; then
      psql_su "$db" -v a="$acct" -v o="$owners" >/dev/null <<'SQL'
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'a') \gexec
SELECT format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I', :'a') \gexec
SELECT format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I', :'a') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I', :'o', :'a') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO %I', :'o', :'a') \gexec
SQL
    fi
  done
  psql_su "$db" >/dev/null <<'SQL'
REVOKE ALL ON SCHEMA public FROM PUBLIC;
SQL
done
echo "question-authoring-db: 계정·데이터베이스·권한을 선언대로 만들었다" >&2
