# 변경 이력

## 미배포 (2026-10-28) 3차 점검 M2

- 시험 수정: `test_purge_threaded_with_uploads_never_loses_a_referenced_source`가 간헐 실패하던 원인은 시험의 가정이었다(방금 올린 참조 전 원본까지 강제로 오래된 것으로 만듦). 올림~참조 저장 구간과 나이 올리기를 배타적으로 하고, purge 뒤 불변식 검사를 추가했다. 코드 변경 없음.
- 환자 정보 탐지: 날짜 치환이 숫자(+구분자 하나, 또는 `)`) 바로 뒤의 날짜꼴은 지우지 않게 해, `010-20120101`·`+82 10 20120101`·`02-20120101`·`031-19991231` 전화번호가 다시 경고된다. 괄호 지역번호 `(02) 2012-0101`도 탐지. 날짜 문장은 계속 경고 없음.

## 0.7.1 (2026-10-28) 2차 독립 점검 반영

- gateway 이메일형 사용자 ID 허용(`@`를 `:at:`로 기록). purge의 조건부 삭제와 재업로드 시 보존 기간 갱신. SIGTERM이 진행 중 요청을 유예 시간(8초)까지 기다림. `purge`도 운영 구성 검사. 환자 정보 탐지에서 날짜 뒤 숫자를 전화로 오인하지 않음.

## 0.7.0 (2026-10-28) 전수 점검 반영과 D88·D89

- P1: 추출기 선형화(첫 콜론 분할, 집합, 줄·문항·슬롯·플래그·시간 한도). P2: `clinical_basis_approved` 등 엄격 파싱(문자열 400). P3/U9: 4-eyes 기본, 초안 소유 검사, CR-0006. P4/U4: 보존·정리 `purge`. P5: 환자 정보 탐지 확대와 비UTF-8 경고. P6: 운영 구성 거부(`QA_DEV_MODE`). P7: 동시 요청 상한, 인증 전 본문 미수신, SIGTERM. M-6: v2 Trace 문항 ID. A7: 초안 잠금, 번호 예약 해제. E2: gateway 토큰 + `X-Openit-User`. D89: `docs/adr/` 12건.
- 스모크는 4-eyes 흐름을 확인한다(저자 자기 승인 403, 검수자 승인).

## 0.6.3 (2026-10-28) 저자 경로·개선 신호의 장애 동작 (FAULT_MATRIX E2, D5)

- 큰 자료(원본 자료, 초안, 버전 쓰기)는 별도 문장 상한 `QA_DB_LARGE_STATEMENT_TIMEOUT_SECONDS`(30초). 8MiB 삽입은 이 PC에서 0.09초 이하라 3초가 끊지는 않았지만 실제 환경 여유를 위해 분리했다.
- HTTP: 요청 소켓 무응답 상한(30초), 업로드가 중간에 끊기면 처리하지 않음, 잘못된 `Content-Length` 400, 예상 밖 오류는 연결 끊김 대신 JSON 500.
- 개선 신호: 호출 5초, 총 마감 20초, 깨진 응답은 503, trace 읽기의 상류 장애·인증 실패는 "trace 없음"으로 세지 않고 실패.
- 환자 정보 경고용 이메일 패턴이 긴 한 줄에서 제곱으로 느려지던 것을 고침(64KB 한 줄 7초에서 0.02초).
- 시험 추가: `tests/test_fault_paths.py`, PostgreSQL 전용 시험(큰 자료 상한, 8MiB 삽입, 트랜잭션 도중 연결 사망의 원자성, 재시작).

## 0.6.2 (2026-10-28) PostgreSQL 대기 상한과 /readyz 상한 (FAULT_MATRIX P0-3 / E1)

- 원인(측정): `docker pause`로 얼린 PostgreSQL에서 이전 코드는 120초 넘게 돌아오지 않았다. 문장 상한이 없었고 `/readyz`(`is_ready`)가 주 연결과 락을 써서 무응답 DB에 같이 묶였다. 접속 상한은 고정 5초였다.
- 접속 3초, 문장 3초(서버 `statement_timeout`; 얼어붙은 서버는 +1초 뒤 클라이언트가 소켓을 닫음), `/readyz`는 주 연결·락을 쓰지 않는 별도 연결로 2초. 설정: `QA_DB_CONNECT_TIMEOUT_SECONDS`, `QA_DB_STATEMENT_TIMEOUT_SECONDS`, `QA_READYZ_DB_TIMEOUT_SECONDS`. 소켓을 닫아 끝낸 호출은 바로 재시도하지 않는다. SQLite 불변.
- 시험: pause/연결 불가 주소/락 대기/설정 검증 추가(PostgreSQL 없으면 SKIP). 컨테이너 조합에서의 재측정은 deploy E2E 5p에서 헤드가 한다.

## 0.6.1 (2026-10-27) PostgreSQL 연결 끊김 복구

- 끊김 오류(클라이언트 쪽, 08류, 57P01~03)만 연결을 다시 열고 같은 호출을 한 번 재시도. 쓰기 재시도는 advisory lock 트랜잭션을 처음부터 다시 열며, 모든 쓰기가 멱등이라 커밋 응답을 잃은 경우에도 중복이 없다. 복구 불가면 `UNAVAILABLE`, 접속 정보 비노출 유지.
- 중복이던 `compose.dev.yaml`, `scripts/dev-postgres.sh` 삭제. 시험용 DB는 `../deploy/scripts/dev-postgres.sh up qa`.

## 0.6.0 (2026-10-27) PostgreSQL 저장소 (표준 서버 DB)

- `PostgresQuestionStore` 추가(`psycopg[binary]` 3.3.6 고정, lock·pyproject). 선택은 `QA_DATABASE_URL`(비밀, `_FILE` 우선)이 있으면 PostgreSQL, 없으면 `QA_DB_PATH` SQLite. 표준을 고른 이유: 서비스별 데이터베이스·계정과 표준 백업·접근 제어를 쓰려고.
- 쓰기는 advisory lock 아래 한 트랜잭션(SQLite의 BEGIN IMMEDIATE와 같은 의미), 불변 트리거, 멱등 스키마 생성. 접속 문자열은 오류·로그·`/readyz`에 쓰지 않는다. `/readyz`에 `db` 종류만 표시.
- 적합성 시험을 `store` 픽스처로 매개변수화(memory, sqlite, sqlite-file, postgres). `TEST_DATABASE_URL`이 없으면 postgres는 SKIP. 동시 쓰기 시험, PostgreSQL 전용 시험, 비밀 비노출 시험 추가.
- `compose.dev.yaml`, `scripts/dev-postgres.sh`, `SMOKE_DB=postgres` 스모크 추가.

## 0.5.0 (2026-10-27) 비밀은 파일로 (D84)

- boundary-contracts v2.0.2로 올림(`pipeline_schema_version` 2.0). 토큰 3종을 `boundary_contracts.read_secret`으로 읽는다(`NAME_FILE` 우선, 없으면 `NAME`). 스모크는 비밀을 파일로 주입하고 `inspect`/`exec env`에 평문이 없음을 확인한다. `tests/test_secrets.py` 추가(로그·/readyz·오류 본문·예외 메시지에 값 없음). 개선 신호 시험 데이터를 v2.0으로 갱신.

## 0.4.0 (2026-10-15) 개선 신호 수신함 (D52)

- `GET /v1/signals`: I7 실패 사례로 문항 세트·문항별 건수, 유형 분포, 혼동, CONFIRMED 수를 낸다(읽기 전용, 저자 인증, 미설정 시 DISABLED). 서버 대역 시험 추가.

## 0.3.1 (2026-10-15)

- boundary-contracts v1.2.0. `HttpQuestionReader`로 우리 서버를 읽는 I1 서버 적합성 시험 추가(여러 쪽 목록, reviewed_only, 해시 재계산, NOT_FOUND, PERMISSION_DENIED, INTEGRITY_ERROR, 서버 중단 UNAVAILABLE). 서버 수정은 필요 없었다.

## 0.3.0 (2026-10-15) HTTP 읽기 면 (D49, D50)

- I1 HTTP 읽기 면(HTTP.md 표): 읽기 전용 토큰 `QA_READ_TOKENS`, 응답 스키마·해시 검증, 오류 코드 422 정정(UNSUPPORTED_VERSION, INTEGRITY_ERROR).
- 문진 스키마는 `openit-survey-schema`(survey-schema-v1.0.0) wheel로 설치. `QA_SURVEY_SCHEMA` 삭제, `build/survey-schema` 복사 방식 삭제.
- boundary-contracts v1.1.0. 이미지 빌드 스크립트 `scripts/build_wheels.sh`(두 태그를 git archive).
- 이미지: HEALTHCHECK, 라벨 `io.openit.*` 6개로 통일.
- 스모크에 읽기 호출과 헬스 확인 추가(포트는 Docker 배정).

## 0.2.0 (2026-10-15) 헤드 결정 반영

- boundary-contracts v1.0.0 사용: 자체 스키마 위치 코드를 지우고 `boundary_contracts.validator`를 쓴다. `QA_BOUNDARY_SCHEMAS` 삭제.
- I1에 `list_ids(cursor, limit)` 추가(SQLite, 메모리, 적합성 시험).
- 이미지: `git archive v1.0.0`으로 만든 wheel 설치(`scripts/build_contract_wheel.sh`), vendor 복사 삭제.
- 스모크: 호스트 포트를 Docker가 배정, 컨테이너 이름에 리포지토리 이름.
- CR-0001~0005 승인.

## 0.1.0 (2026-10-15) 첫 구간

- 문항 저장소: `AuthoringStore` 인터페이스, SQLite 구현(불변 트리거, 읽기 전용 연결), 메모리 구현. I1 `QuestionReader`를 두 구현이 같은 적합성 시험으로 통과.
- 저작 서비스: 초안, 편집, 플래그 확인, 미리보기, 저자 확인(ReviewRecord), 이후 검수, 환자 정보 경고.
- ChangeClassification 생성(문구 / 선택지·단위 / 슬롯 / 분기, 영향 슬롯, 후속 작업).
- 문항 자료 등록: 추출기 인터페이스와 레지스트리, 텍스트 양식 추출기, FALLBACK, 원본 해시 출처 기록.
- 얇은 HTTP 계층, Dockerfile, 스모크 시험.
- 문서: design, boundary, runbook, STATUS, 변경 요청 5건.
