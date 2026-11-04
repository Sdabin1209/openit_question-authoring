#!/usr/bin/env bash
# 개발·시험용 일회용 PostgreSQL(이 리포의 compose.dev.yaml). 사용: dev-postgres.sh up | down | status
#   up   : postgres:16 을 띄우고 접속 문자열 한 줄을 표준 출력에 낸다(안내는 표준 오류). 이미 떠 있으면 같은 문자열을 다시 낸다(멱등). 호스트 포트는 Docker 가 배정한다.
#   down : 컨테이너와 임시 비밀 폴더를 지운다(이 스크립트가 만든 프로젝트만).
# 환경: DEV_PG_SUFFIX(같은 리포에서 여러 개를 동시에 띄울 때 구분 이름). 컨테이너·프로젝트 이름에 리포 이름을 넣는다(여러 영역이 같은 서버에서 동시에 돌 때 충돌하지 않게).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
CMD="${1:-}"
SUFFIX="${DEV_PG_SUFFIX:+-$DEV_PG_SUFFIX}"
PROJECT="question-authoring-dev-pg${SUFFIX}"
STATE="$here/.dev-postgres/${PROJECT}"
export QA_DEV_PG_STATE="$STATE"
# shellcheck disable=SC1091
QA_PG_IMAGE="$(. "$here/images.env"; printf %s "$POSTGRES_IMAGE")"; export QA_PG_IMAGE   # 이미지 고정값의 정본은 images.env
compose() { docker compose -p "$PROJECT" -f "$here/compose.dev.yaml" "$@"; }
dsn() {
  local port pw
  port="$(compose port postgres 5432 | tr -d '\r' | sed 's/.*://')"
  pw="$(cat "$STATE/pg_password")"
  printf 'postgresql://qa:%s@127.0.0.1:%s/qa\n' "$pw" "$port"
}
case "$CMD" in
  up)
    mkdir -p "$STATE"
    if [ ! -s "$STATE/pg_password" ]; then
      ( umask 077; head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' > "$STATE/pg_password" )
      chmod 644 "$STATE/pg_password"       # 컨테이너 안의 postgres 사용자가 읽는다(일회용 값, 폴더는 .gitignore)
    fi
    compose up -d --wait >&2
    dsn
    ;;
  down)
    compose down -v --remove-orphans >&2
    rm -rf "$STATE"
    echo "내렸다: $PROJECT" >&2
    ;;
  status)
    compose ps
    ;;
  *) echo "사용: dev-postgres.sh up|down|status" >&2; exit 2;;
esac
