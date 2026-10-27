#!/usr/bin/env bash
# 개발·시험용 PostgreSQL 컨테이너 관리. 사용: dev-postgres.sh up|url|down
#   up   : 띄우고 준비될 때까지 기다린다(출력에 비밀 없음)
#   url  : TEST_DATABASE_URL로 쓸 접속 문자열을 stdout에 낸다(암호 포함이므로 로그에 남기지 말 것)
#   down : 컨테이너와 볼륨을 지운다
# 사용 예: export TEST_DATABASE_URL="$(scripts/dev-postgres.sh url)"; .venv/bin/pytest
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$here/.dev"; chmod 700 "$here/.dev"
envf="$here/.dev/pg.env"
if [ ! -f "$envf" ]; then
  umask 077
  printf 'QA_DEV_PG_PASSWORD=%s\n' "$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')" > "$envf"
fi
set -a; . "$envf"; set +a
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
