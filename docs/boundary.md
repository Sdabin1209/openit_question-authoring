# 경계 문서: question-authoring

## 소유하는 저장소

- 문항 저장소. 운영은 PostgreSQL(서비스별 데이터베이스·계정, `QA_DATABASE_URL`), 개발·시험 기본은 SQLite 단일 파일(`QA_DB_PATH`, 기본 `/data/questions.sqlite3`). 같은 인터페이스 뒤의 두 구현이고 같은 적합성 시험을 통과한다. 표준 서버 DB를 고른 이유: 영역별 파일 DB보다 백업·접근 제어·동시 쓰기를 표준 도구로 다룰 수 있다. 불변 문항 세트 버전, 검수(`ReviewRecord`), 변경 분류(`ChangeClassification`)와, 가변 작업 영역인 초안·원본 자료.
- 쓰기는 저작 API(`AuthoringService`) 하나다. 관리자 콘솔도 저작 API의 클라이언트로만 쓴다.
- 서비스의 Question Bank는 갱신하지 않는다. 게이트를 거친 릴리스 묶음이 전달한다.

## 제공하는 것

| 이름 | 내용 | 확인 |
| --- | --- | --- |
| I1 QuestionReader HTTP 읽기 면 | `../boundary-contracts/HTTP.md` 표 그대로: `GET /v1/question-sets`, `/{id}/versions`, `/{id}/latest?reviewed_only=`, `/{id}/versions/{n}`, `.../reviews`, `.../change`. 목록 `{items, next_cursor}`(limit 기본 100, 최대 500), 없을 수 있는 것은 `{item: null}`. 응답 레코드는 스키마 검증과 해시 재계산을 거친다. 읽기 토큰(`QA_READ_TOKENS=역할=토큰`)은 읽기 면만 호출할 수 있고 저작 경로는 403. 오류 상태 코드는 CONVENTIONS 9절(UNSUPPORTED_VERSION, INTEGRITY_ERROR는 422). | `tests/test_http_read.py`, `tests/test_http_client_conformance.py`(HttpQuestionReader, 서버 적합성), 스모크 |
| I1 QuestionReader (`boundary_contracts.sdk.QuestionReader`) | `get_question_bank`, `latest(reviewed_only)`, `list_ids`, `list_versions`, `get_reviews`, `get_change`. 구현: `question_authoring.store.PostgresQuestionStore`(운영), `SqliteQuestionStore`(개발·시험), 메모리 구현 `MemoryQuestionStore`. 모두 `.reader()`로 읽기 전용 뷰를 낸다. | 같은 적합성 시험을 통과 |
| 저작 API (HTTP) | design.md 8절 | `tests/test_http_api.py`, `scripts/smoke.sh` |
| 생산하는 계약 | `QuestionBank`(기존), `ReviewRecord`, `ChangeClassification` | 쓰기 전에 스키마 검증 |

이웃 영역의 사용법: 시험에는 `MemoryQuestionStore().reader()`를 쓰고, 값을 넣으려면 `AuthoringService`로 초안 -> 확인을 거치게 한다(`tests/helpers.py`의 `publish` 참고). 운영에서는 `PostgresQuestionStore(url, read_only=True).reader()`(읽기 계정). (SDK를 별도 패키지로 나누고 Git 태그로 배포하는 일은 헤드와 정할 문제로 남겼다. STATUS.md.)

## 소비하는 것과 지원 버전

- 문진 스키마 `QuestionBank`: `openit-survey-schema`(태그 survey-schema-v1.0.0)의 정수 버전 1만 지원한다. 설치되어 있지 않거나 다르면 `UNSUPPORTED_VERSION`.
- 경계 계약: `ReviewRecord`, `ChangeClassification`, `ArtifactRef`, `BoundaryError`를 `pipeline_schema_version` `2.0`(boundary-contracts v2.0.2)으로 쓴다. 해시는 `boundary_contracts.canonical`로만 계산한다.
- I7 FailureStore(ops-feedback): HTTP로 읽기만 한다(`list_cases`, `get_trace`). 설정 시에만 켜진다. 장애 시 개선 신호 조회만 실패하고 저작·I1 읽기는 영향이 없다. 비식별 trace의 구조 정보만 쓰며 발화는 저장·반환하지 않는다.
- 아직 소비하지 않는 것: I4(판정 조회), I8(규칙 미리보기). 미구현이며 해당 계약이 필요한 시점에 이 문서에 추가한다.

## 이 영역이 지키는 것

- 문항 세트 버전은 저자가 미리보기로 확인해야 만들어지고, 그 확인이 `ReviewRecord`(`clinical_basis_approved`=true)로 남는다. 같은 `(id, version)`의 내용은 바뀌지 않는다(저장소 트리거와 읽을 때 해시 재계산).
- `reviewed_only`는 가장 최근 검수가 APPROVED이면서 `clinical_basis_approved`가 true인 버전만 낸다(계약 문구보다 보수적인 해석, CR-0004).
- 변경마다 `ChangeClassification`(둘째 버전부터). 모든 버전의 출처는 `ReviewRecord.ext.question-authoring.source`에 있다. 읽는 영역은 `ext`를 판단에 쓰지 않는다.
- 오류는 모두 `BoundaryError`. 읽기 SDK는 입력 형식이 틀리면 `INVALID_RECORD`, 없으면 `NOT_FOUND`, 해시 불일치는 `INTEGRITY_ERROR`.

## 장애 영향

| 장애 | 영향 | 비고 |
| --- | --- | --- |
| 저작 서비스 중단 | 새 문항 등록·수정이 멈춘다. 이미 만든 버전을 읽는 것은 읽기 전용 SQLite 연결로 계속 된다. 서비스(survey-service)는 영향 없음 | 서비스는 게이트가 묶은 릴리스 사본만 쓴다 |
| 저장소(PostgreSQL 연결 끊김/SQLite 파일) 읽기 실패 | I1 호출이 `UNAVAILABLE`(재시도 가능). 게이트 제출·합성 데이터 생성이 지연된다 | `/readyz`가 503 |
| PostgreSQL이 TCP는 받되 응답이 없거나 느림(FAULT_MATRIX P0-3/E1) | `/readyz`가 `QA_READYZ_DB_TIMEOUT_SECONDS`(기본 2초) 안에 503(`db: postgres`, 접속 정보 없음). `/healthz`는 200. 요청 경로(저작·I1 읽기)는 아래 상한 안에 `UNAVAILABLE`로 끝난다 | DB가 돌아오면 서비스를 다시 띄우지 않아도 다음 `/readyz`가 200이고 다음 호출이 새 연결로 성공 |
| 저장소 내용 변조 | 해당 버전 읽기가 `INTEGRITY_ERROR`. 다른 버전은 영향 없음 | 백업 복원은 runbook |
| ops-feedback 중단 | `GET /v1/signals`만 UNAVAILABLE. 나머지 영향 없음 |
| 이웃 영역(release-gate, turn-engine) 중단 | 영향 없음. 호출하지 않는다 | 이후 구간에서 이 표를 갱신 |
| 계약 버전 불일치 | 시작하지 않는다(`UNSUPPORTED_VERSION`) | |

## DB 대기 상한 (PostgreSQL, FAULT_MATRIX P0-3 / E1)

| 설정 | 기본 | 뜻 |
| --- | --- | --- |
| `QA_DB_CONNECT_TIMEOUT_SECONDS` | 3 | 접속 시도 상한. 이전 고정값은 5초. libpq는 정수 초이고 2초 미만을 2초로 본다 |
| `QA_DB_STATEMENT_TIMEOUT_SECONDS` | 3 | 문장 하나의 상한(서버 쪽 `statement_timeout`, 쓰기 직렬화 advisory lock 대기 포함). 이전에는 없었다 |
| `QA_READYZ_DB_TIMEOUT_SECONDS` | 2 | `/readyz`의 DB 확인 상한 |

- 서버가 얼어 응답이 없으면(예: `docker pause`) 서버 쪽 `statement_timeout`은 동작하지 못한다. 그래서 저장소 호출 하나가 문장 상한 + 1초 안에 끝나지 않으면 클라이언트가 소켓을 닫아 `UNAVAILABLE`로 끝낸다. 이 경우는 같은 서버에 곧바로 다시 걸지 않는다(상한을 두 배로 만들지 않기 위해). 끊김 복구 재시도(연결이 끊긴 오류만 한 번)는 그대로다. 따라서 얼어붙은 서버에서 호출 하나의 최악은 약 4초(3+1), 그다음 호출은 접속 상한인 약 3초에서 끝난다. 시작 때 스키마 생성만 문장 상한 30초를 쓴다(다른 인스턴스의 락을 기다릴 수 있다).
- `/readyz`는 저장소의 주 연결과 락을 쓰지 않고 별도의 짧은 연결 하나로 형식 표지를 읽는다(쓰기가 락을 기다리는 중에도 막히지 않는다). 작업 스레드를 기다리다 상한이 되면 소켓을 닫고 503을 낸다.
- 기본값의 근거[제안, 이 영역이 정한 값]: 정상일 때 문장은 수 ms이고 쓰기 하나는 몇 문장이라 3초면 느린 디스크·잠깐의 락 대기는 견디고 이웃(게이트, 합성 데이터)의 호출 대기보다 짧다. 2초는 로드 밸런서·오케스트레이터 검사 제한이 보통 수 초이고 libpq 접속 최소 단위가 2초라서다. 관리자만 쓰는 영역이라 상한을 보수적으로 짧게 잡았고, 큰 원본 자료(`blobs`) 삽입이 문장 상한을 넘으면 설정을 올려야 한다.
- SQLite 경로는 바뀌지 않았다.
- 확인한 것(이 PC, 일회용 PostgreSQL 컨테이너 한 대, 이 영역 시험): `docker pause` 중 `/readyz` 확인 2.0초에 실패, 열려 있던 연결로 읽기 4.0초 `UNAVAILABLE`, 새 접속 3.0초 `UNAVAILABLE`, 응답 없는 주소(10.255.255.1)도 상한 안에 503, 해제 뒤 즉시 복구. 이전 코드는 같은 `docker pause`에서 120초 넘게 돌아오지 않았다(중단하고 되돌림). 시험은 `tests/test_postgres_specific.py`.
- 확인하지 못한 것: 컨테이너 조합(gateway, 다른 영역과 함께)에서의 재측정은 deploy E2E 5p에서 헤드가 한다. 호출자(이웃 영역)가 이 영역의 HTTP 응답을 기다리는 시간과의 맞물림, 진짜 네트워크 단절(패킷 손실)과 느린 디스크는 시험하지 않았다. 문장 하나가 길게 걸리는 큰 쓰기의 실제 분포는 측정하지 않았다.

## 변경 요청

`docs/change-requests/` 참고: CR-0001 출처 위치, CR-0002 QuestionBank.version 의미, CR-0003 SlotRef 문자열, CR-0004 의미 해석(reviewed_only, 후속 작업 매핑), CR-0005 계약 패키지 배포(스키마 포함, 태그). 헤드가 모두 승인했다(CR 파일 상단 참고).
