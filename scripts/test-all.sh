#!/usr/bin/env bash
# 영역 전체 시험: 일회용 PostgreSQL(scripts/dev-postgres.sh up, 호스트 포트는 Docker 배정)을 띄워 TEST_DATABASE_URL 로 넘기고 pytest 전체를 돌린 뒤 내린다.
# 왜: 운영 저장소는 PostgreSQL 이다. TEST_DATABASE_URL 이 없으면 PostgreSQL 시험이 건너뛰어지는데, 환경 보고의 test_command 가 pytest 만 가리켜
# deploy 의 사전 점검(check-all)이 PostgreSQL 시험을 한 번도 돌리지 않았다(2026-11-07). 이 명령이 환경 보고(SetupEnvReport.test_command)의 정본이다.
# 종료 코드는 pytest 의 것이다. 인자는 pytest 에 그대로 넘긴다.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-.venv/bin/python}
# 이미 TEST_DATABASE_URL 이 있으면(이 명령을 시험 안에서 다시 부를 때 등) 새로 띄우지 않고 그대로 쓴다: 새로 띄우고 내리면 바깥 시험의 PostgreSQL 을 내리게 된다
if [ -z "${TEST_DATABASE_URL:-}" ]; then
  # 실행마다 고유한 프로젝트 이름을 띄우기·내리기에 같게 준다: 개발용 프로젝트(접미사 없음)와 다른 사본의 실행을 건드리지 않는다(2026-11-07 교차검증)
  export DEV_PG_SUFFIX="t$$-$(date +%s)"
  trap 'bash scripts/dev-postgres.sh down >/dev/null 2>&1 || true' EXIT   # 소유 프로젝트가 정해진 직후, 띄우기 전에 건다: 띄우다 실패해도 만든 것을 지운다(교차검증)
  URL=$(bash scripts/dev-postgres.sh up) || { echo "오류: 시험용 PostgreSQL 을 띄우지 못했다" >&2; exit 2; }
  [ -n "$URL" ] || { echo "오류: 시험용 PostgreSQL 접속 문자열이 비었다" >&2; exit 2; }
  export TEST_DATABASE_URL="$URL"
fi
"$PY" -m pytest -q -p no:cacheprovider "$@"
