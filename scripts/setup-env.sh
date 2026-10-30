#!/usr/bin/env bash
# 왜: 영역 환경 준비의 정본은 이 파일 하나다. 새 머신으로 옮길 때 README·런북마다 설치법이 달라(이웃 폴더 editable 등) 혼자 설치되지 않았기 때문이다.
# 근거: 워크스페이스 규칙(의존은 Git 태그 설치 + 잠금 파일). 버전·태그의 정본은 requirements.lock이며 여기에는 적지 않는다.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."   # 어느 폴더에서 실행해도 리포 루트 기준

# 계약 SetupEnvReport(boundary-contracts 2.6.0, U43): 표준 출력의 마지막 줄은 결과 JSON 한 줄이다. 설치 진행 기록은 모두 표준 오류로 보낸다.
# 멱등이고 인자·비밀이 없다. 종료 코드는 0 성공, 아니면 실패(실패해도 보고 줄은 낸다).
start=$SECONDS
rc=0
(
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
  retry "${PIP[@]}" install -q pytest setuptools wheel || exit 1   # setuptools·wheel: 빌드 진입점 시험이 네트워크 없이 wheel을 만든다
  # 타입 생성 도구(datamodel-code-generator). tests/test_structure.py가 types/를 다시 생성해 비교하므로 시험 명령에 필요하다. 버전은 requirements-gen.lock이 정한다
  retry "${PIP[@]}" install -q -r requirements-gen.lock || exit 1
  # 이 리포 자체. 의존은 위에서 lock대로 설치했으므로 --no-deps.
  "${PIP[@]}" install -q -e . --no-deps || { echo "setup-env: 이 리포 설치 실패" >&2; exit 1; }

  echo "setup-env: OK ($(pwd)/.venv)"
) >&2 || rc=$?
if [ $rc -eq 0 ]; then
  printf '{"area":"question-authoring","status":"ok","exit_code":0,"seconds":%d,"environment":{"kind":"python-venv","path":".venv","test_command":[".venv/bin/python","-m","pytest","-q"]}}\n' "$((SECONDS - start))"
else
  printf '{"area":"question-authoring","status":"fail","exit_code":%d,"seconds":%d}\n' "$rc" "$((SECONDS - start))"
fi
exit $rc
