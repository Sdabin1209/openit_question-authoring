# STATUS (2026-10-29, 패키지 버전은 pyproject.toml이 정본: 0.1.0)

> 문서에 남은 0.5.0~0.7.0은 날짜순 작업 묶음의 이름이고 패키지 버전이 아니다. 패키지 버전은 `pyproject.toml`과 `question_authoring/__init__.py`의 0.1.0 하나다.

## 한 일
- design.md, boundary.md, runbook.md, CHANGELOG.md, 변경 요청 5건.
- I1 QuestionReader: PostgreSQL(운영)·SQLite(개발·시험)·메모리 구현, 같은 적합성 시험을 모두에서 통과(PostgreSQL은 `TEST_DATABASE_URL` 필요, 없으면 SKIP).
- 저작 서비스 핵심 로직, ChangeClassification 생성, 추출기 인터페이스와 텍스트 양식 추출기, 얇은 HTTP 계층.
- Dockerfile, 스모크 시험(`scripts/smoke.sh`, `docker`로 실행해 SMOKE OK 확인).
- 후속 2: I1 HTTP 읽기 면, 읽기 토큰, survey-schema wheel, HEALTHCHECK, 라벨 통일. boundary-contracts는 v2.0.2(clients 포함).
- `python -m pytest -q`: PostgreSQL 포함 전체 통과(0.6.0, `TEST_DATABASE_URL` 설정 시). 미설정 시 PostgreSQL 항목은 SKIP.
- model-runtime: 이 영역은 설치하지 않고 코드에서도 쓰지 않는다. 이미지 라벨 `io.openit.model-runtime`은 `n/a`다(Dockerfile). 규칙 미리보기(I8)는 계약 3.0.0에서 퇴역한 turn-engine 몫이라 이 영역에서 하지 않는다.

- 0.6.2: PostgreSQL 접속·문장·`/readyz` 대기 상한(FAULT_MATRIX P0-3/E1). pytest 218개 통과(`TEST_DATABASE_URL` 설정), 미설정 시 160 통과 + 58 SKIP. 스모크 SQLite·PostgreSQL 모두 SMOKE OK. 컨테이너 조합 재측정은 deploy E2E 5p(헤드).

- 0.6.3: E2·D5 장애 동작(docs/boundary.md), 큰 자료 쓰기 상한, 이메일 패턴 제곱 지연 수정. pytest 283개 통과(`TEST_DATABASE_URL` 설정), 미설정 206 통과 + 77 SKIP. 운영 구성에서 저자 경로를 열지는 사용자 결정(E2)이라 손대지 않았다.

- 2026-10-29 단위 3: 계약 v2.2.1 핀(`requirements.lock`, `scripts/build_wheels.sh`), 문진 스키마 survey-schema-v1.1.0 핀(태그는 헤드가 단다). 저작 API 요청은 `AuthoringRequests`로 검사하고 **모르는 키를 거부**한다(동작 변경: 이전에는 무시). 응답은 `AuthoringResponses`·`SignalsResponse`·`ReadyzResponse`로 자기 검사(어기면 503). 환자 확인·검수의 `ext`는 `QuestionAuthoringExt`로 쓰기 전에 검사. 환경 변수는 `QuestionAuthoringDeployInterface.Environment`로 시작 때 검사. `purge` 출력은 JSON 한 줄(`PurgeResult`). 결함 수정: 추출기 선택 전에 실패한 자료의 출처에 `extractor: null`이 들어가 계약을 어기던 것. 시험 개수는 README "시험 상태"(이번 실행 값은 그곳에 갱신).

- 2026-10-29 단위 4a: HTTP 서버를 WSGI+waitress로(ADR 0016), 스키마를 yoyo 마이그레이션으로(ADR 0017), 계약 핀 v2.2.2. 동작 변경은 docs/CHANGELOG.md의 해당 두 항목.

## 남은 일
- 관리자 콘솔 UI, 화면 입력용 템플릿(필드 목록)과 코드·단위 사전(내용은 저자가 정한다).
- 소비 계약: I4 판정 조회, I8 규칙 미리보기 연동. (I7 개선 신호는 구현함. 문항 ID를 trace의 planner_output.directive에서 읽는다는 가정은 ops-feedback과 확인 필요.)
- PDF·Word 추출기(텍스트 추출 + 기존 문법 파서 재사용 가능). 실제 저자가 쓰는 형식을 확인해야 한다.
- I1 SDK를 별도 설치 가능한 패키지로 분리하고 Git 태그로 배포하는 방법(헤드와 협의).
- 저자 인증의 실제 방식(현재는 정적 토큰 표).
- 운영 PostgreSQL의 데이터베이스·계정 발급과 `QA_DATABASE_URL_FILE` 비밀 배치는 deploy 몫(이 리포는 변수 계약만 정함). 읽기 전용 계정 분리는 미정.
- 기존 SQLite 파일 데이터를 PostgreSQL로 옮기는 도구는 없다(운영 데이터가 아직 없다고 가정, 확인 필요).

## 막힌 것
- 없음. 변경 요청 CR-0001~0005는 모두 승인됨.
