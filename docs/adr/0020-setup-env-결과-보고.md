# ADR 0020: setup-env.sh의 결과 보고 한 줄

- 상태: 채택 (2026-10-30, U43)

## 맥락
deploy가 이 영역의 `.venv/bin/python` 경로와 시험 도구(pytest)를 짐작했다(doctor.sh, check_all.py). 구조 규칙 1 위반이다(OPEN_GAPS U43). 계약 2.6.0 `SetupEnvReport`가 영역 쪽 선언을 정했다.

## 결정
`bash scripts/setup-env.sh`가 표준 출력 마지막 줄에 `SetupEnvReport` JSON 한 줄을 낸다: `{area, status, exit_code, seconds, environment:{kind:"python-venv", path:".venv", test_command:[".venv/bin/python","-m","pytest","-q"]}}`. 설치 진행 기록은 모두 표준 오류로 보낸다. 실패해도 `status:"fail"` 보고 줄을 내고 같은 종료 코드로 끝난다(`environment`는 없다).

## 왜
- deploy는 `test_command`만 읽고 이웃의 환경 경로를 짐작하지 않는다(계약 설명). 경로와 argv는 이 영역이 정한다(`.venv`는 이 스크립트가 만들고, 시험 전체는 `pytest -q`).
- 표준 출력에 보고만 두는 이유: 호출자가 마지막 줄만 파싱해도 pip 출력이 섞이지 않는다.
- 설치 단계는 서브셸에 묶어 표준 출력을 표준 오류로 돌린다. 설치 명령마다 `>&2`를 붙이면 빠뜨린 하나가 보고를 깬다.
- 멱등: 이미 `.venv`가 있으면 재사용하고 핀대로 다시 설치한다(`--force-reinstall`).

## 검토한 대안과 버리는 이유
- 보고를 파일로 씀: 읽는 쪽이 경로를 알아야 해서 같은 문제가 생긴다.

## 결과
시험(`tests/test_setup_env.py`)은 pip를 가짜 python으로 대신해 보고 줄과 실패 경로를 본다. 진짜 설치는 깨끗한 clone에서 직접 돌려 확인한다. 서브셸이 `||` 왼쪽에 있어 안쪽 `set -e`는 꺼지므로 설치 단계는 `|| exit 1`로 직접 멈춘다(venv 생성 실패는 이어지는 pip 실패로 드러난다).
