#!/usr/bin/env bash
# 왜: 영역 환경 준비의 정본은 이 파일 하나다. 새 머신으로 옮길 때 README·런북마다 설치법이 달라(이웃 폴더 editable 등) 혼자 설치되지 않았기 때문이다.
# 근거: 워크스페이스 규칙(의존은 Git 태그 설치 + 잠금 파일). 버전·태그의 정본은 requirements.lock이며 여기에는 적지 않는다.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."   # 어느 폴더에서 실행해도 리포 루트 기준

PY="${PYTHON:-python3}"
[ -d .venv ] || "$PY" -m venv .venv      # 있으면 재사용(멱등)
PIP=(.venv/bin/python -m pip)

# 네트워크(git 태그, 패키지 색인) 실패는 일시적일 수 있어 한 번만 재시도한다.
retry() {
  "$@" && return 0
  echo "setup-env: 실패, 5초 뒤 한 번 재시도: $*" >&2
  sleep 5
  "$@" && return 0
  echo "setup-env: 실패(네트워크 또는 github 접근 확인): $*" >&2
  return 1
}

# --force-reinstall: 오래된 설치(예: 이웃 폴더 editable)가 남아 있어도 핀과 같아지게 한다.
retry "${PIP[@]}" install -q --force-reinstall -r requirements.lock || exit 1
# 시험 도구. 버전은 lock이 정하지 않은 도구라 최신을 쓴다(psycopg는 lock에 있다).
retry "${PIP[@]}" install -q pytest || exit 1
# 이 리포 자체. 의존은 위에서 lock대로 설치했으므로 --no-deps.
"${PIP[@]}" install -q -e . --no-deps || { echo "setup-env: 이 리포 설치 실패" >&2; exit 1; }

echo "setup-env: OK ($(pwd)/.venv)"
