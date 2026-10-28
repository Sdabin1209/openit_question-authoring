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

- 문진 스키마 `QuestionBank`: `openit-survey-schema`(태그는 `requirements.lock`이 정본)의 정수 버전 1만 지원한다. 설치되어 있지 않거나 다르면 `UNSUPPORTED_VERSION`.
- 사용하는 비밀(D84, `NAME_FILE`로 파일 경로를 받는다; deploy `compose.yaml`의 이 서비스 환경과 같은 이름): `QA_AUTHOR_TOKENS_FILE`, `QA_GATEWAY_TOKENS_FILE`, `QA_READ_TOKENS_FILE`, `QA_FAILURES_TOKEN_FILE`, `QA_DATABASE_URL_FILE`. 값은 어디에도 적지 않는다.
- 경계 계약: `ReviewRecord`, `ChangeClassification`, `ArtifactRef`, `BoundaryError`를 `boundary_contracts.PIPELINE_SCHEMA_VERSION`으로 쓴다(버전 정본은 `requirements.lock`). 해시는 `boundary_contracts.canonical`로만 계산한다.
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

## 저자 클라이언트와 개선 신호 읽기의 장애 (FAULT_MATRIX E2, D5)

기대 동작은 FAULT_POLICY 원칙 1·4·9·10·12를 이 영역에 적용한 것이다. 운영 구성에서 저자 경로(E2)를 열지 말지는 사용자 결정이라 이 영역은 포트·경로·설정을 열지 않았다(아래 표는 이 서비스의 동작이지 운영 노출 여부가 아니다).

**E2 저자 클라이언트 -> 저작 API (코드와 시험으로 확인한 것)**

| 칸 | 동작 | 시험 |
| --- | --- | --- |
| F1 서버 중지 / F8 분리 | 클라이언트 쪽 일이다. 서버가 돌아오면 같은 요청을 다시 보내면 된다(아래 멱등) | F4 시험 |
| F2 무응답 / F3 느림 (업로드가 멈춤) | 요청 소켓이 `QA_REQUEST_TIMEOUT_SECONDS`(기본 30초) 멈추면 연결을 끊고 처리하지 않는다. 이전에는 상한이 없어 멈춘 업로드가 스레드를 계속 쥐었다 | `test_stalled_upload_is_cut_and_nothing_is_saved` |
| 업로드 도중 연결 끊김 | 본문이 `Content-Length`보다 짧으면 처리하지 않는다(초안·원본 자료 저장 없음). 이전에는 짧게 온 본문을 그대로 처리했다 | `test_client_disconnect_mid_upload_saves_nothing` |
| F4 서비스 재시작 | 초안·확정 버전은 저장소(PostgreSQL/SQLite)에 있어 재시작 뒤에도 미리보기와 확인을 이어 갈 수 있다. 확인을 두 번 보내면 같은 `ref`, 다른 미리보기 해시면 409 `CONFLICT` | `test_restart_keeps_drafts...`(SQLite), `test_postgres_restart_keeps_draft...` |
| F5 5xx | 저장소 장애는 503 `UNAVAILABLE`(재시도 가능). **예상 밖의 오류는 이전에 연결을 끊고 응답이 없었으나 이제 JSON 503(`UNAVAILABLE`, 원문 메시지 없음)이다(CONVENTIONS 9절 표에 500이 없다)** | `test_unexpected_error_is_json_503...` |
| F6 깨진 요청 | JSON이 아니면 400, `Content-Length`가 숫자가 아니거나 음수면 400(이전에는 연결이 끊김), 8MiB 초과는 waitress가 413으로 거절한다(ADR 0016; 계약 v2.2.2 CONVENTIONS 9절의 전송 계층 한도) | `test_bad_content_length_is_400`, `test_oversize_is_413_before_the_body_is_sent` |
| F7 인증 실패 | 토큰이 없거나 틀리면 401 + `WWW-Authenticate: Bearer`, 토큰은 맞지만 권한이 없으면 403(읽기 토큰으로 저작 경로, 저자 역할이 아님, gateway 경유에서 모르는 사용자). gateway 토큰인데 `X-Openit-User`가 없으면 401. 본문 `code`는 어느 쪽이든 `PERMISSION_DENIED`, `retryable=false`(계약 CONVENTIONS.md 6절, D106; 이전에는 모두 403이었다). 실패를 세어 `/readyz`에 보이는 것은 하지 않았다 | `test_auth_failures_are_not_retryable` |

- **원자성(원칙 1)**: 문항 세트 버전, 검수, 변경 분류는 한 트랜잭션이다. 트랜잭션 도중(삽입이 끝난 뒤 커밋 직전) 서버 쪽에서 연결이 죽는 상황을 PostgreSQL에서 흉내 내 보면, 두 번 다 죽으면 버전·검수 모두 남지 않고(`UNAVAILABLE`), 첫 시도만 죽으면 재시도가 끝까지 가서 정확히 한 번 저장된다(`test_commit_is_atomic_when_connection_dies_mid_transaction`). 원본 자료(`blobs`)는 초안 생성 전에 따로 저장되므로 업로드가 중간에 실패하면 주인 없는 원본이 남을 수 있다(내용 해시로 식별되고 같은 자료를 다시 올리면 그대로 쓴다. 해가 없지만 청소하는 도구는 없다).
- **멱등(원칙 2에 해당)**: 같은 버전·같은 내용의 `commit_version`, 같은 `review_id`·같은 내용의 `add_review`는 조용히 성공하고, 같은 ID에 다른 내용이면 `CONFLICT`다(세 저장소 구현 모두, `test_resend_same_ok_different_conflict`). **한계**: `POST /v1/materials`를 같은 파일로 다시 보내면 새 초안이 하나 더 생긴다(문항 세트 버전이 아니라 가변 작업 영역이라 값이 이중 저장되는 일은 없지만 초안이 중복된다).
- **큰 자료(원본 자료, 초안, 문항 세트 버전 쓰기)의 문장 상한**: 일반 문장 상한(3초)과 별도로 `QA_DB_LARGE_STATEMENT_TIMEOUT_SECONDS`(기본 30)를 쓴다. 서버가 받는 최대 본문(`MAX_BODY` 8MiB)을 이 PC의 일회용 PostgreSQL(메모리 디스크, 루프백)에서 `blobs`에 넣는 데 걸린 시간은 최대 0.09초였다(무작위 8MiB 0.09초, 반복 텍스트 8MiB 0.05초, 1/4MiB는 더 짧다). 즉 이 환경에서는 3초가 정상 업로드를 끊지 않았다. 그래도 별도 상한을 둔 이유: 이 측정은 빠른 디스크·루프백 한 환경이고, 실제 서버의 디스크·네트워크 DB에서 3초 안이라는 보장이 없으며 정상 업로드가 `UNAVAILABLE`로 실패하는 것이 더 나쁘기 때문이다[제안, 이 영역이 정한 값]. 락 대기는 이 상한을 받지 않는다(락을 얻은 뒤에만 늘린다). 실제 저자 자료의 크기 분포는 모른다.
- **처리 시간(DB가 아닌 CPU)**: 환자 정보 경고용 이메일 패턴이 긴 한 줄에서 제곱으로 느려져 64KB 한 줄에 7초가 걸렸다. 길이를 묶어 고쳤다(1MB 한 줄 0.27초). 그 밖의 추출기·검증의 시간은 8MiB 규모로 측정하지 않았다.

**D5 question-authoring -> ops-feedback(I7 읽기)**

| 상황 | 동작 | 시험 |
| --- | --- | --- |
| 상류 느림 | 호출 하나의 읽기 상한 `QA_FAILURES_TIMEOUT_SECONDS`(기본 5초; 이전에는 클라이언트 기본 10초). 목록·trace를 여러 번 부르는 한 번의 `GET /v1/signals`에는 총 마감 `QA_SIGNALS_DEADLINE_SECONDS`(기본 20초)를 걸었다(이전에는 최대 수십 번 × 10초). 넘으면 부분 결과 없이 503 | `test_d5_slow_upstream...`, `test_d5_total_deadline...` |
| 상류 5xx | 503 `UNAVAILABLE`(재시도 가능). 저작·I1 읽기는 영향 없음 | `test_d5_upstream_5xx...` |
| 깨진 응답(JSON 아님, 형식 틀림) | 성공으로 취급하지 않고 503 `UNAVAILABLE` | 같은 시험 |
| trace 읽기 실패 | `NOT_FOUND`처럼 그 사례만 못 읽는 경우는 건너뛰고 세지만, 상류 장애(`UNAVAILABLE`)·인증 실패는 이제 건너뛰지 않고 전체를 실패시킨다(이전에는 "trace 없음"으로 조용히 세어 OK 응답이 나갔다) | `test_d5_trace_upstream_failure...` |
| 상류 인증 실패(토큰 불일치) | 403 `PERMISSION_DENIED`로 나간다. 호출한 저자의 인증 실패와 구별되지 않는다(메시지로만 구분). `/readyz`에 건수로 드러내는 것은 하지 않았다 | `test_i7_failure_maps_to_boundary_error` |

확인하지 못한 것: 컨테이너 조합(gateway, 실제 ops-feedback 이미지)에서의 측정은 deploy E2E에서 헤드가 한다. 실제 네트워크 단절·패킷 손실, 매우 큰(수 MB) 실제 저자 자료로 한 PDF·Word 추출(미구현), 실제 서버의 디스크 속도에서의 삽입 시간, HTTP 서버가 동시에 받을 수 있는 업로드 수의 상한(스레드당 한 연결, 상한 없음)은 시험하지 않았다.

## 인증, 4-eyes, gateway 경유 (D88 U9, E2 준비)

- **4-eyes(기본)**: 저자의 `confirm`은 `clinical_basis_approved=false`로 기록한다. 임상 근거 승인은 `clinical-reviewer` 역할이 저자와 다른 주체 ID로 `add_review`할 때만 true가 된다(자기 승인은 403). 초안은 만든 주체만 편집·확인·폐기한다. 이웃이 보는 변화: `reviewed_only`에 버전이 나타나는 시점이 검수자 승인 뒤다. 계약 의미에 닿는 부분은 `docs/change-requests/CR-0006`. 끄는 설정(`QA_REQUIRE_TWO_PERSON=false`)은 `/readyz`에 경고가 뜬다. ADR 0006.
- **E2 저자 경로를 운영에서 열 때(deploy가 gateway 경로를 만든다) 서비스가 신뢰하는 것**: (1) gateway는 사람 인증(Basic 등)을 마친 요청에 `Authorization: Bearer <QA_GATEWAY_TOKENS의 토큰>`과 `X-Openit-User: <인증된 사용자 ID>`를 붙이고, 클라이언트가 보낸 같은 이름 헤더는 지운다(`boundary-contracts/HTTP.md`, `PUBLIC_API.md`의 규약). (2) 이 서비스는 gateway 토큰의 요청에서만 `X-Openit-User`를 사용자로 쓰고 정적 토큰의 요청에서는 무시한다. (3) 사용자의 역할은 `QA_USER_ROLES`(사용자ID=역할)로 이 서비스가 정한다(역할 헤더는 계약에 없다). (4) 서비스 포트는 직접 노출하지 않는다(authoring 네트워크 안). (5) 읽기 면(I1)은 읽기 토큰으로 계속 닿는다. 열지 말지는 사용자 결정(E2)이며 이 영역은 열지 않았다. ADR 0010.
- **gateway 사용자 ID**: 일반 ID 또는 이메일형(`local@domain`, 문자 `A-Za-z0-9._-`, 길이 각 60자 이하)만 받는다. 계약의 `Id`(ReviewRecord.reviewer_id)는 `@`를 허용하지 않으므로 기록할 때 `@`를 `:at:`로 바꾼다(예 `kim@hosp.kr` -> `kim:at:hosp.kr`). 입력의 `:`는 허용하지 않아 충돌이 없다. 이전에는 `_principal`이 `@`를 받고 `_ID`가 거부해 이메일형 ID가 400이었다.
- **SIGTERM**: 새 요청을 받지 않고 진행 중 요청을 `QA_SHUTDOWN_GRACE_SECONDS`(기본 8초, docker stop 기본 10초보다 짧게; 측정 안 함)까지 기다린 뒤 종료한다. 이전 주석은 "마친다"였으나 실제로는 기다리지 않았다. 유예를 넘기면 남은 수를 기록하고 종료하며, 확인 도중이면 `confirming` 예약이 남지만 다음 호출이 복구한다.
- **운영 구성 검사**: `QA_DEV_MODE=1`이 아니면 DB URL과 토큰이 없을 때 시작을 거부한다(이전에는 tmpfs SQLite로 조용히 떴다). deploy 확인 필요: 운영 compose는 PostgreSQL URL 파일과 토큰 파일을 준다고 읽었으나 이 영역에서 실행해 확인하지 않았다. 시험 구성(compose.test.yaml)이 SQLite를 쓰면 `QA_DEV_MODE=1`이 필요하다.

## 보존과 정리 (D88 U4, ADR 0011)

`python -m question_authoring purge`(run-once, 멱등): 처리되지 않은(OPEN, DISCARDED) 초안은 마지막 수정 30일 뒤, 어떤 버전·초안도 참조하지 않는 원본 자료(고아 포함)는 저장 30일 뒤 지운다. 버전·검수·변경 분류·확정된 초안은 지우지 않는다. 일수는 설정이고 무기한은 거부한다. 삭제는 확인과 삭제를 한 문장(같은 쓰기 락·트랜잭션)으로 한다: 저장 시각이 오래됐고 어떤 버전·초안도 참조하지 않을 때만 지우며, 같은 자료를 다시 올리면 저장 시각이 갱신된다(새 초안·재업로드와 겹쳐도 참조 중인 원본을 지우지 않는다). 초안은 읽은 그대로일 때만 지운다. `purge`도 운영 구성 검사(`QA_DEV_MODE` 아니면 DB URL 필수, 토큰은 요구하지 않음)를 거친다. 30일은 잠정 기본값(병원 정책 미정). 스케줄은 deploy 몫이며 아직 연결되지 않았다.

## 변경 요청

`docs/change-requests/` 참고: CR-0001 출처 위치, CR-0002 QuestionBank.version 의미, CR-0003 SlotRef 문자열, CR-0004 의미 해석(reviewed_only, 후속 작업 매핑), CR-0005 계약 패키지 배포(스키마 포함, 태그). 헤드가 모두 승인했다(CR 파일 상단 참고).
