# question-authoring (문항 저작)

의료인이 올린 문항 자료를 검토·확인을 거쳐 "바꿀 수 없는 문항 버전"으로 저장하고, 이웃 영역이 읽게 해 주는 서비스다.

이 영역은 문진 시스템에서 문항(무엇을 어떻게 묻는지)의 유일한 저장 창구다. 저자가 텍스트 양식 자료를 올리면 초안으로 바꾸고, 확신하지 못한 값(수치, 단위, 범위, 위험 기준)에는 표시를 달아 저자가 미리보기로 직접 확인하게 한다. 저자가 확인해야만 문항 세트 버전이 생기고 그 확인이 검수 기록(`ReviewRecord`)으로 남는다. 버전은 한 번 만들면 고치지 않고, 수정은 새 버전이다(docs/design.md 1절).
하지 않는 것: 임상 기준의 내용을 정하지 않는다(저자의 몫, D30). 모델 학습·평가와 릴리스 승인을 하지 않는다. 서비스(survey-service)의 Question Bank를 직접 갱신하지 않는다(게이트를 거친 릴리스 묶음이 전달, docs/brief.md 6절). 관리자 콘솔 화면은 아직 없다(docs/STATUS.md).

## 전체 속의 위치

| 방향 | 상대 | 내용 | 인터페이스 |
| --- | --- | --- | --- |
| 출력 | 합성 데이터, model-lab, 게이트(docs/brief.md 2절) | 문항 세트 불변 버전, 검수, 변경 분류 읽기 | I1 QuestionReader. HTTP `GET /v1/question-sets` 이하(docs/boundary.md) |
| 입력 | ops-feedback | 실패 사례로 만드는 개선 신호(읽기 전용, 설정할 때만 켜짐) | I7 FailureStore(`list_cases`, `get_trace`) |
| 입력(미구현) | release-gate | 판정 조회 | I4 |
| 입력 | 사람(저자) | 문항 자료 파일, 화면 입력, 확인 | 저작 API `/v1/drafts`, `/v1/materials` 등(docs/design.md 8절) |

이웃 영역의 코드는 가져오지 않는다. 계약은 `boundary-contracts`, 문진 스키마는 `openit-survey-schema` 패키지로만(버전은 `requirements.lock`과 `deploy/scripts/lib.sh`가 정본) 쓴다(docs/boundary.md, requirements.lock).

## 구조

| 경로 | 역할 |
| --- | --- |
| `schemas/` | 영역 내부 타입의 JSON Schema와 통과·거부 예시, "스키마 밖 조건" 표([schemas/README.md](schemas/README.md)) |
| `question_authoring/types/` | `schemas/`에서 생성한 타입(손으로 고치지 않는다, 생성 명령은 아래 "타입 생성") |
| `question_authoring/components/` | 컴포넌트 한 파일씩(업무, 저장소, `link_*` 통신). 목록과 입출력은 [docs/components.md](docs/components.md) |
| `question_authoring/wiring/` | 컴포넌트 연결: 저작 서비스(`service.py`), 스키마 검증 껍데기, 저장소 주입 |
| `question_authoring/__main__.py`, `store/migrate.py`, `migrations/` | 진입점(환경 변수는 여기서만 읽음), 마이그레이션. 컴포넌트가 아니다 |
| `tests/` | 시험(저장소 적합성, HTTP, 추출기, 비밀 값, 구조 `test_structure.py` 등) |
| `scripts/gen_types.py`, `scripts/smoke.sh`, `scripts/build_wheels.sh` | 타입 생성, 이미지 스모크 시험, 계약 wheel 빌드 |
| `Dockerfile`, `requirements.lock`, `requirements-gen.lock` | 이미지와 잠긴 의존(계약·통신 라이브러리 태그), 타입 생성기 잠금 |
| `examples/question_form.txt` | 텍스트 양식 입력 예 |
| `docs/` | 설계·경계·런북 등(아래 "더 자세한 문서") |

타입 생성(datamodel-code-generator 0.25.0, `requirements-gen.lock`에 고정): `python3 -m venv .gen && .gen/bin/pip install -r requirements-gen.lock && .gen/bin/python scripts/gen_types.py --contracts "$(.venv/bin/python -c 'import boundary_contracts;print(boundary_contracts.schema_dir())')"`. `tests/test_structure.py`가 다시 생성한 결과와 `types/`가 같은지 본다.

```
자료 파일(.txt/.md) --> 환자정보 경고 --> 추출기 --> 초안(+확신 못 한 값 표시)
                                             \-- 실패하면 빈 초안(FALLBACK) --> 화면 입력
초안 --> 편집/표시 확인 --> 미리보기 --> 저자 확인(confirm)
     --> 한 번에 저장: 문항 세트 버전 + ReviewRecord + (2번째 버전부터) ChangeClassification
저장소(SQLite) --> I1 읽기 (이웃 영역, 읽기 전용 토큰)
```

## 실행 방법

전제: Python 3.12(Dockerfile 기준), git, github ssh 접근(계약과 문진 스키마를 `requirements.lock`의 태그로 받는다). 이웃 폴더는 필요 없다. 스모크에는 Docker(데몬 접근 가능)와 curl이 필요하다.

설치와 시험(docs/runbook.md):
```
bash scripts/setup-env.sh        # .venv 생성·재사용, requirements.lock 태그로 설치, pytest, -e . --no-deps
. .venv/bin/activate
python -m pytest -q
# PostgreSQL 시험(선택): export TEST_DATABASE_URL="$(bash ../deploy/scripts/dev-postgres.sh up qa)"; 끝나면 ... down qa  (없으면 해당 시험은 SKIP)
```
이미지 스모크(빌드, 기동, `/healthz`, `/readyz`, 등록-미리보기-확인, 인증 거부, 비root, 라벨 확인. 포트는 Docker가 배정):
```
scripts/smoke.sh
```
서비스 실행: `python -m question_authoring`. 기본 DB 경로가 `/data/questions.sqlite3`라 로컬에서는 `QA_DB_PATH`를 쓰기 가능한 경로로 줘야 한다(이 세션에서 `--help`로 시도했더니 `/data` 권한 오류로 멈췄고, `--help` 옵션은 없다).
비밀(`QA_AUTHOR_TOKENS`, `QA_READ_TOKENS`, `QA_FAILURES_TOKEN`)은 값을 환경 변수로 주지 않고 파일 경로를 `NAME_FILE`로 준다(D84). 예: `-e QA_AUTHOR_TOKENS_FILE=/run/secrets/author_tokens`. 환경 변수 전체 목록은 docs/runbook.md.
이 세션에서 실제로 돌린 것은 docs/STATUS.md와 CHANGELOG 참고(수치는 그때그때 `python -m pytest -q -rs`로 확인).
이 세션에서 돌린 것과 시험 개수는 "현재 상태" 절의 시험 상태 문단이 정본이다(개수는 이 줄에 복사하지 않는다). `python -m question_authoring --help`는 위 오류로 멈췄다.
돌리지 않은 것: `scripts/smoke.sh`(이미지 빌드), 서버 기동. 새 clone·새 venv 설치와 시험은 "시험 상태" 문단대로 했다.

## 설계 결정 기록(ADR)

구조 선택의 이유는 [docs/adr/](docs/adr/README.md)에 있다: 저장소(SQLite 개발, PostgreSQL 운영), 불변 버전과 content_hash, 원본 자료 보관, 선형 줄 파서, 환자 정보 정규식 경고, 저자와 다른 검수자(4-eyes), 한도와 timeout 값, 확인 흐름과 예약, 개선 신호, 인증과 gateway, 보존 정리, HTTP 서버. 근거를 찾지 못한 값은 ADR에 '근거 기록 없음'으로 적었다.

## 현재 상태

**시험 상태(정본, 개수는 여기 한 곳)**: 2026-10-30, 구조 개편(U400) 커밋, 개발 venv(requirements.lock 그대로)에서 PostgreSQL 포함 전체 `python -m pytest -q -rsf`: 717 통과, 0 건너뜀, 0 실패(약 164초). 이미지: `scripts/build-image.sh` 1회 통과, `scripts/smoke.sh` SMOKE OK (sqlite). 깨끗한 두 번째 환경에서의 재현은 하지 않았다.

2026-10-28 묶음(옛 표기 0.7.0, 패키지 버전은 pyproject의 0.1.0; 시험 개수는 위 "시험 상태" 문단, SQLite·PostgreSQL 스모크 SMOKE OK): 전수 점검 반영(추출기 선형화, 4-eyes 기본, 엄격 입력, 환자 정보 탐지 확대, 운영 구성 거부, 동시 요청 상한, SIGTERM, 보존 `purge`, v2 Trace 문항 ID). 상세와 한계는 docs/CHANGELOG.md, docs/boundary.md.

저자 경로·개선 신호의 장애 동작(옛 표기 0.6.3, 2026-10-28, FAULT_MATRIX E2·D5): 멈춘/끊긴 업로드, 깨진 요청, 예상 밖 오류, 재시작·재전송 멱등, 큰 자료 쓰기 상한, 개선 신호의 느림·5xx·깨진 응답. 칸별 표와 한계는 docs/boundary.md. 확인한 것(이 PC, 일회용 PostgreSQL 한 대): 시험 개수는 "시험 상태" 문단(이 줄의 당시 값은 복사하지 않는다). 운영에서 저자 경로를 열지 말지는 사용자 결정이라 열지 않았다. 컨테이너 조합 재측정은 deploy E2E에서 헤드가 한다.

PostgreSQL 대기 상한(옛 표기 0.6.2, 2026-10-28, FAULT_MATRIX P0-3/E1): 접속 3초, 문장 3초, `/readyz` DB 확인 2초(설정 가능, 근거는 docs/boundary.md "DB 대기 상한"). 확인한 것(이 PC, 일회용 PostgreSQL 컨테이너): 시험 개수는 "시험 상태" 문단. `SMOKE_DB=postgres scripts/smoke.sh`와 기본(SQLite) `scripts/smoke.sh` 모두 SMOKE OK. `docker pause`에서 `/readyz`는 2.0초에 503, 해제 뒤 즉시 200. 확인하지 못한 것: 컨테이너 조합(gateway, 다른 영역)에서의 재측정은 deploy E2E 5p에서 헤드가 한다.

확인된 것(출처 docs/STATUS.md, docs/CHANGELOG.md):
- 옛 표기 0.5.0(2026-10-27) 비밀 파일 방식(D84)과 당시 기준 계약 적용. 당시 시험 개수는 STATUS 기록이고 현재 값은 "시험 상태" 문단이다.
- STATUS는 스모크 `SMOKE OK`를 기록하고 있으나 이 세션에서 다시 확인하지 않았다.
- 구현: I1 읽기(SQLite, 메모리, HTTP), 저작 서비스, 텍스트 양식 추출기, 변경 분류, 개선 신호 수신함(I7).
미구현(docs/STATUS.md, docs/design.md 12절): 관리자 콘솔 UI, 화면 입력 템플릿과 코드·단위 사전, PDF·Word 추출기, I4·I8 연동, 저자 인증의 실제 방식(지금은 정적 토큰 표), I1 SDK의 별도 패키지·태그 배포.
확인 못 한 것: 실제 저자가 쓰는 파일 형식(docs/design.md 7.1절), 개선 신호가 읽는 trace 필드를 ops-feedback과 맞춘 것(STATUS가 "확인 필요"라고 적음), 다른 PC·Linux 서버에서의 동작.

## 이 영역의 열린 항목

헤드 대장(`../head/docs/OPEN_GAPS.md`, 읽기 전용으로 읽음)에서 영역 칸에 question-authoring이 적힌 항목은 확인하지 못했다. 이 영역에 직접 걸린 열린 ID는 없다.
다만 전 영역에 걸친 U06(일반 Linux 서버 설치 미확인)과 U12(두 번째 독립 머신에서의 영역 시험 미확인)는 이 영역에도 해당한다(사용자·환경 몫). 대장에 없는 이 영역의 남은 일은 STATUS의 "남은 일"이다: 콘솔·템플릿·사전(저자와 헤드 협의), PDF·Word 추출기(실제 저자 형식 확인 필요), I4·I8(상대 계약 필요), 인증 방식(결정 필요).

## 더 자세한 문서

| 문서 | 내용 |
| --- | --- |
| `docs/brief.md` | 영역 목적, 소유·제공·소비, 수용 기준, 건드리면 안 되는 경계 |
| `docs/design.md` | 내부 설계: 저장소, 확인 절차, 변경 분류 표, 추출기와 텍스트 양식 문법, 저작 API 표 |
| `docs/boundary.md` | 소유 저장소, 제공 SDK·HTTP, 지원 버전, 장애 영향 |
| `docs/runbook.md` | 개발 환경, 스모크, 비밀 파일(D84), 환경 변수, 흔한 상황 |
| `docs/STATUS.md` | 한 일, 남은 일, 막힌 것 |
| `docs/CHANGELOG.md` | 버전별 변경 이력 |
| `docs/change-requests/` | 헤드에게 낸 변경 요청 CR-0001~0005(모두 승인) |
| `CLAUDE.md` | 이 영역 에이전트용 지침 |
