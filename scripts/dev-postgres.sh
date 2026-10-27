#!/usr/bin/env bash
# 개발·시험용 PostgreSQL 컨테이너 관리. 사용: dev-postgres.sh up|url|down
#   up   : 띄우고 준비될 때까지 기다린다(출력에 비밀 없음)
#   url  : TEST_DATABASE_URL로 쓸 접속 문자열을 stdout에 낸다(암호 포함이므로 로그에 남기지 말 것)
#   down : 컨테이너와 볼륨을 지운다
# 사용 예: export TEST_DATABASE_URL="$(scripts/dev-postgres.sh url)"; .venv/bin/pytest
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$here/.dev"; chmod 700 "$here/.dev"
pwf="$here/.dev/pg_password"
if [ ! -f "$pwf" ]; then
  umask 077
  head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' > "$pwf"
fi
chmod 644 "$pwf"   # 컨테이너 안 postgres 사용자가 읽는다. 디렉터리(.dev)가 700이라 다른 사용자는 못 읽는다
QA_DEV_PG_PASSWORD="$(cat "$pwf")"
dc() { docker compose -f "$here/compose.dev.yaml" "$@"; }
case "${1:-}" in
  up)
    dc up -d --wait >/dev/null
    echo "postgres 준비됨" >&2 ;;
  url)
    port=$(dc port postgres 5432 | head -1 | sed 's/.*://')
    [ -n "$port" ] || { echo "postgres가 떠 있지 않다: dev-postgres.sh up" >&2; exit 1; }
    printf 'postgresql://qa:%s@127.0.0.1:%s/question_authoring\n' "$QA_DEV_PG_PASSWORD" "$port" ;;
  down)
    dc down -v >/dev/null ;;
  *) echo "사용: $0 up|url|down" >&2; exit 2 ;;
esac
