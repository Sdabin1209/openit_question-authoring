# 런북: question-authoring

## 개발 환경

```
bash scripts/setup-env.sh   # 환경 준비의 정본(멱등). 이웃 폴더 불필요
. .venv/bin/activate
python -m pytest -q
```

경계 계약 스키마는 `boundary_contracts`, 문진 스키마는 `openit-survey-schema` 패키지의 것을 쓴다(버전 정본은 `requirements.lock`과 `deploy/scripts/lib.sh`). 복사본은 없다.

## 이미지 스모크 시험 (D22)

```
scripts/smoke.sh                 # docker가 있을 때
DOCKER=podman scripts/smoke.sh   # 실행 파일 이름만 바꿀 수 있다. 데몬에 못 닿으면 오류로 중단한다
```

`build/`에 계약 wheel을 만들고(`scripts/build_wheels.sh`: 두 태그 시점 소스(git archive)로 만든 wheel, 커밋하지 않음) 이미지를 빌드해 컨테이너를 띄운 뒤 `/healthz`, `/readyz`, 자료 등록 -> 미리보기 -> 확인, 인증 없는 요청 거부, 비root 실행, 라벨을 확인한다. 변경을 완료했다고 말하기 전에 돌린다.

## 비밀은 NAME_FILE로 (D84)

토큰 같은 비밀(QA_AUTHOR_TOKENS, QA_READ_TOKENS, QA_FAILURES_TOKEN)을 환경 변수 값으로 주면 `docker inspect`와 `docker exec env`에 평문이 보인다. 운영에서는 값을 파일에 두고 그 경로만 `NAME_FILE`로 준다(예: `-v /srv/secrets/qa:/run/secrets:ro -e QA_AUTHOR_TOKENS_FILE=/run/secrets/author_tokens`). 코드는 `boundary_contracts.read_secret(NAME)`으로 읽는다: `NAME_FILE`이 있으면 그 파일(앞뒤 공백 제거), 없을 때만 `NAME` 환경 변수(로컬 개발용)를 쓴다. 파일이 없거나 비면 시작을 거부하며 오류 메시지에는 경로만 있고 값은 없다. 비밀 값은 로그·`/readyz`·오류 본문에 쓰지 않으며 `tests/test_secrets.py`가 확인한다. 파일은 컨테이너 사용자(uid 10001)가 읽을 수 있어야 한다.

## 설정(환경 변수. 진입점 `python -m question_authoring`만 읽는다)

| 이름 | 뜻 | 기본 |
| --- | --- | --- |
| QA_DATABASE_URL(`_FILE` 권장) | PostgreSQL 접속 문자열(`postgresql://계정:암호@호스트:5432/데이터베이스`). 있으면 PostgreSQL을 쓴다. 암호가 들어 있으므로 비밀 값이다 | 없음 |
| QA_DB_PATH | QA_DATABASE_URL이 없을 때만 쓰는 SQLite 파일(개발·시험) | /data/questions.sqlite3 |
| QA_READ_TOKENS(`_FILE` 가능) | `역할=토큰` 쉼표 목록. I1 읽기 전용 토큰(저작 경로는 403, 토큰이 틀리면 401). 비밀 값 | 없음(읽기 면 토큰 없음·틀림은 401) |
| QA_FAILURES_URL | ops-feedback(I7) URL. 비우면 `/v1/signals`는 DISABLED | 없음 |
| QA_FAILURES_TOKEN_ENV | I7 토큰의 비밀 이름. 이 이름의 `_FILE`(또는 개발용 환경 변수)을 읽는다 | QA_FAILURES_TOKEN |
| QA_AUTHOR_TOKENS(`_FILE` 가능) | `토큰:actor-id:역할` 쉼표 목록. 비밀 값 | 없음(모든 요청 401) |
| QA_HOST, QA_PORT | 바인드 | 0.0.0.0, 8080 |
| QA_DB_CONNECT_TIMEOUT_SECONDS | PostgreSQL 접속 상한(초). 근거와 의미는 docs/boundary.md "DB 대기 상한" | 3 |
| QA_DB_STATEMENT_TIMEOUT_SECONDS | 문장 하나의 상한(초). 호출 하나는 이 값 + 1초 안에 끝난다 | 3 |
| QA_DEV_MODE | 1이면 개발·시험 구성(SQLite, 토큰 없이)을 허용. **없으면 운영 구성이어야 시작한다**: `QA_DATABASE_URL(_FILE)`과 저자 토큰(`QA_AUTHOR_TOKENS` 또는 `QA_GATEWAY_TOKENS`)이 없으면 시작을 거부 | 없음(운영) |
| QA_REQUIRE_TWO_PERSON | 저자 != 검수자(4-eyes). false면 이전 동작이고 `/readyz`에 `warnings: ["two_person_disabled"]` | true |
| QA_AUTHOR_TOKENS(`_FILE`) 역할 | `question-author`(자료 등록·편집·확인), `clinical-reviewer`(임상 근거 승인, 조회). 다른 역할은 시작 거부 | |
| QA_GATEWAY_TOKENS(`_FILE`) | gateway가 쓰는 서비스 토큰 목록. 이 토큰의 요청만 `X-Openit-User`를 신뢰한다 | 없음 |
| QA_USER_ROLES | `사용자ID=역할` 쉼표 목록(gateway 경유 사용자의 역할표) | 없음 |
| QA_MAX_CONCURRENT_REQUESTS | waitress 처리 스레드 수(동시 처리 요청 상한). 넘는 요청은 줄 서서 기다린다(ADR 0016) | 32 |
| QA_DRAFT_RETENTION_DAYS, QA_SOURCE_RETENTION_DAYS | `python -m question_authoring purge`의 보존 일수. 양의 정수만(무기한 불가). 표준 출력은 JSON 한 줄 `{"drafts_removed": N, "sources_removed": M}`(계약 PurgeResult) | 30, 30 |

환경 변수의 형식(숫자, 열거, `QA_USER_ROLES` 역할 목록)은 시작 때 계약 `QuestionAuthoringDeployInterface.Environment`로 검사하고, 어기면 변수 이름과 규칙만 말하고(값은 말하지 않는다) 시작을 거부한다. 빈 문자열은 설정 안 함으로 본다. 불리언은 `1/0/true/false` 소문자만 받는다(이전에는 `TRUE`도 받았다).
| QA_DB_LARGE_STATEMENT_TIMEOUT_SECONDS | 큰 자료(원본 자료, 초안, 문항 세트 버전) 쓰기의 문장 상한(초) | 30 |
| QA_REQUEST_TIMEOUT_SECONDS | HTTP 요청 소켓이 이 시간 멈추면 끊는다(멈춘 업로드) | 30 |
| QA_FAILURES_TIMEOUT_SECONDS | I7 호출 하나의 읽기 상한(초) | 5 |
| QA_SIGNALS_DEADLINE_SECONDS | `GET /v1/signals` 한 번의 총 마감(초) | 20 |
| QA_REQUEST_DEADLINE_SECONDS | 사람용 요청 하나의 서버 쪽 전체 마감(초). 각 DB 문장은 남은 시간을 상한으로 쓰고, 넘기면 저장하지 않고 503(D120, ADR 0025) | 12 |
| QA_READYZ_DB_TIMEOUT_SECONDS | `/readyz`의 DB 확인 상한(초) | 2 |

로그는 표준 출력. 운영 데이터는 PostgreSQL 서버에 있다(서비스별 데이터베이스·계정, 이 서비스 계정에는 이 데이터베이스만 준다). SQLite 경로를 쓸 때만 `/data` 볼륨이 필요하다. `/readyz`는 DB 종류(`"db": "sqlite"|"postgres"`)만 보여주고 접속 정보는 절대 쓰지 않는다. 시작 시 스키마를 멱등하게 만든다(`CREATE TABLE IF NOT EXISTS`, advisory lock 아래). 버전·검수·변경 분류는 DB 트리거가 UPDATE/DELETE를 막는다.

## PostgreSQL 시험

`export TEST_DATABASE_URL="$(bash ../deploy/scripts/dev-postgres.sh up qa)"`(deploy의 일회용 postgres, 빈 포트, `qa` 계정·데이터베이스, 암호는 일회용 임의값) 후 `python -m pytest -q -rs`. `TEST_DATABASE_URL`이 없으면 PostgreSQL 시험은 SKIP이며 통과로 세지 않는다. 시험마다 임시 스키마를 만들고 지운다. 연결 끊김 복구 시험은 `pg_terminate_backend`로 서버 프로세스를 끊어 확인한다. 이미지 스모크의 PostgreSQL 경로: `SMOKE_DB=postgres bash scripts/smoke.sh`(임시 네트워크에 postgres를 띄움). 끝나면 `bash ../deploy/scripts/dev-postgres.sh down qa`.

## 흔한 상황

- DB 서버 재시작/연결 끊김: 서비스 재시작이 필요 없다. 끊김 오류(클라이언트 쪽 끊김, SQLSTATE 08류, 57P01~03)만 연결을 다시 열고 같은 호출을 한 번 재시도한다. 쓰기는 한 트랜잭션이고 모든 쓰기 연산이 멱등이라(커밋 응답만 잃어도 재시도가 중복을 만들지 않는다) 안전하다. 재연결도 실패하면 `UNAVAILABLE`(`/readyz` 503)이고 DB가 돌아오면 다음 호출에서 회복된다.
- 저자가 자료를 올렸는데 FALLBACK: 응답의 `reason`을 보고, 원본이 텍스트 양식인지 확인한다. 화면 입력(`/edits`)으로 이어서 등록할 수 있다. 원본 해시는 출처로 남는다.
- 확인이 `FAILED_PRECONDITION`: (a) 열린 플래그가 남음(응답 `details.flag_ids`), (b) 미리보기 이후 초안이 바뀜(미리보기를 다시 받는다), (c) 초안을 만든 뒤 새 버전이 생김(최신에서 다시 초안), (d) 이전 버전과 내용이 같음.
- `INTEGRITY_ERROR`: 해당 버전의 저장 내용이 기록된 해시와 다르다. 파일을 변조했거나 손상된 것이다. 백업에서 복원한다. 버전을 고쳐 쓰지 않는다.
- 승인 철회: `POST /v1/reviews`로 REJECTED 또는 CHANGES_REQUESTED(사유 필수)를 덧붙인다. 그 버전은 `reviewed_only`에서 빠진다. 다시 승인하려면 APPROVED + clinical_basis_approved를 덧붙인다.
- 백업(PostgreSQL): `pg_dump`로 이 서비스의 데이터베이스를 덤프한다. 복원 후 `PostgresQuestionStore`로 열어 각 버전을 `get_question_bank`로 읽어 해시를 확인한다.
- 백업(SQLite): 서비스를 멈추거나 `sqlite3 questions.sqlite3 ".backup out.sqlite3"`로 복사한다. 복원 후 `SqliteQuestionStore`로 열어 각 버전을 `get_question_bank`로 읽어 해시를 확인한다.
- 새 추출기 추가: `components/extract_textform.py`를 본떠 같은 입출력(MaterialExtractRequest, MaterialExtraction)의 컴포넌트를 만들고 `wiring/service.py`의 `DEFAULT_EXTRACTORS`에 붙인다. 시험은 `tests/test_extractor.py`를 본뜬다. 새 추출기는 추정하지 않는다는 규칙(UNSURE/MISSING/UNPARSED)을 지켜야 한다.

## 이미지 점검

라벨: `io.openit.service`, `io.openit.revision`, `io.openit.contracts.boundary`, `io.openit.contracts.survey-schema`, `io.openit.model-runtime`(D50; 계약 3.0.0에서 turn-engine 라벨은 없어졌다), `io.openit.exposure`. HEALTHCHECK는 `/healthz`를 15초마다 부른다(`docker inspect -f '{{.State.Health.Status}}'`). 스모크는 읽기 토큰으로 I1 읽기 호출과 저작 경로 403도 확인한다.

이미지에는 `schemas/`가 `/app/schemas`로 들어가고 `QA_SCHEMA_DIR`이 그 경로를 가리킨다(Dockerfile). 스키마를 못 찾으면 서비스는 첫 요청에서 실패한다.
