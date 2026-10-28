# question-authoring 구조 규칙 점검 보고서 (2026-10-28, D104)

기준: `head/docs/audit/structure-rules-audit-spec.md`(R1~R7, 점검 항목 A~G). 읽기 전용 점검이며 코드·설정·시험은 고치지 않았다. 점검한 커밋: 이 보고서를 커밋하기 직전의 HEAD.

## 0. 요약

| 심각도 | 개수 |
| --- | --- |
| P1 | 0 |
| P2 | 12 |
| P3 | 7 |

P1(독립성을 깨고 실제 결합을 만드는 것)은 찾지 못했다. 가장 큰 구조 문제는 "컴포넌트 경계가 모듈 이름에만 있고 코드에는 없다"는 것이다(QA-01~05). 계약 대조(F)에서 실제 어긋남 하나를 재현했다(QA-13, 퍼센트 인코딩된 ID).

읽은 것: 이 리포의 CLAUDE.md, README.md, docs/brief.md, design.md, boundary.md, STATUS.md, runbook.md, CR-0006, ADR 목차, `question_authoring/` 전 파일(service, http_api, signals, classify, preview, validation, __main__, extractors/base·__init__, store/base·__init, sqlite·memory·postgres는 grep과 일부만), Dockerfile, scripts/build_wheels.sh, requirements.lock. 계약: `../boundary-contracts`의 HTTP.md, INTERFACES.md(I1, I7, I8), CONVENTIONS.md 9·10절, schemas(ReviewRecord, ChangeClassification, FailureCase), `boundary_contracts/sdk.py`, `clients/questions.py`, `clients/_http.py`(일부), `../survey-service/schema/schemas/TurnTrace.json`(question_id 부분).
돌린 것: `.venv/bin/python -m pytest -q tests/test_http_client_conformance.py tests/test_http_read.py tests/test_signals.py tests/test_reader_conformance.py` -> 71 통과, 23 건너뜀(PostgreSQL 없음). 재현 스크립트 2개(임시 파일, 메모리 저장소). 전체 시험, 스모크(Docker), PostgreSQL 시험은 돌리지 않았다.

## A. 컴포넌트 목록

브리프 8절의 시드와 design.md 2절은 모듈 단위로 컴포넌트를 나열한다. 코드에서 실제로 분리된 단위와 I/O는 다음과 같다. "스키마" 칸은 기계가 읽는 정의가 있는지를 뜻한다.

| 이름 | 파일 | 입력 | 출력 | 업무 | 스키마 |
| --- | --- | --- | --- | --- | --- |
| 추출기(자료 -> 초안) | `extractors/base.py`, `textform.py` | 바이트, 파일명 | `ExtractionResult(bank: dict, flags: list[ExtractedFlag])` | 텍스트 양식을 QuestionBank 모양 초안과 확신 못 한 값 플래그로 바꾼다 | 데이터클래스 + Protocol(`base.py:402-423`). `bank`는 `dict`(약함) |
| 자료 등록 | `service.py` `ingest_material`, `_phi_warnings`(`:289-345`) | 파일, 문항 세트 ID | 초안(dict), FALLBACK | 환자 정보 경고, 원본 보관, 추출기 선택, 플래그 부여 | 없음(dict) |
| 초안 편집·플래그 확인 | `service.py` `apply_edits`, `acknowledge_flags`, `discard_draft`, `apply_edit` | 편집 목록, 플래그 ID | 초안(dict) | JSON 포인터 편집, 저자 확인 | 없음(dict) |
| 미리보기 | `preview.py`, `service.get_preview` | 초안(dict) | 텍스트, `preview_hash`, 변경 요약 | 사람이 읽는 미리보기와 해시 | 없음(dict) |
| 확인(저자 확인 -> 버전) | `service.py` `confirm`(`:373-450`) | 초안, `preview_hash` | `ArtifactRef`, `ReviewRecord`, `ChangeClassification`(저장) | 번호 예약, 검증, 변경 분류 호출, 원자적 저장 | 계약 스키마(ReviewRecord 등)로 검증 |
| 임상 검토(4-eyes) | `service.py` `add_review`(`:452-489`) | `ArtifactRef`, 판단 | `ReviewRecord` | 검수 역할만 임상 승인, 자기 승인 거부 | 계약 스키마 |
| 변경 분류 | `classify.py` | 이전·새 QuestionBank | `ChangeClassification` 또는 None | 필드 표 기반 기계적 분류 | 계약 스키마. 입력은 문진 스키마 |
| 스키마·참조 검증 | `validation.py` | 레코드 | 오류 목록 | 계약·QuestionBank 검증, 참조 무결성 | JSON Schema(계약·문진 스키마 패키지) |
| 문항 저장소(I1 읽기 논리 포함) | `store/base.py`, `memory.py`, `sqlite.py`, `postgres.py` | 쓰기: bank, ref, review, change, source / 읽기: ref | QuestionBank, ref, ReviewRecord, ChangeClassification | 불변 버전 저장, `reviewed_only`, 보존 정리 | 계약 스키마. 초안·원본은 dict |
| I1 HTTP 서버 | `http_api.py` `_read`(`:110-146`) | HTTP 요청 | 계약 레코드 | HTTP.md I1 표 구현 | 계약 스키마 |
| 개선 신호(I7 읽기) | `signals.py` | `FailureSource`(list_cases, get_trace) | 신호 dict | 실패 사례를 문항 세트·문항별로 센다 | Protocol(로컬). 출력은 dict |
| 인증·역할 | `http_api.py` `_principal` 등, `service._auth` | Bearer 토큰, `X-Openit-User` | `Actor(id, role)` | 토큰 -> 행위자 | 데이터클래스 `Actor` |

접착 코드(어느 컴포넌트에도 속하지 않음): `http_api.py`의 `Api._route`(저작 경로 라우팅), `make_server`(소켓, 동시 요청 상한, 종료), `__main__.py`(환경 변수, 비밀 읽기, 조립, SIGTERM, `purge` 잡), `store/__init__.py`.

컴포넌트 경계가 없는 덩어리: `AuthoringService` 한 클래스(`service.py:119`)가 자료 등록, 편집, 미리보기 호출, 확인, 검수, 환자 정보 정규식, 인증 확인을 모두 가진다. `Api` 한 클래스가 인증, I1 서버, 저작 라우팅, 개선 신호를 모두 가진다(QA-01, QA-04).

## B~G. 발견

### QA-01 (R2, R3, P2) 서비스 한 클래스가 여러 컴포넌트의 일을 한다
- 위치: `question_authoring/service.py:119`(`class AuthoringService`), 파일 전체 489줄.
- 인용: `class AuthoringService:` / `def ingest_material(...)`, `def confirm(...)`, `def add_review(...)`
- 왜 위반인가: 자료 등록, 환자 정보 탐지(`_PHI`, `:38-41`), 초안 편집, 미리보기 조립, 확인, 이후 검수가 한 객체의 상태(`self.store`, `self.v`, `self.require_two_person`, 초안 잠금 표)를 공유한다. 컴포넌트 단위로 입력·출력을 정해 따로 시험하거나 바꿀 수 없다. R2의 "작업 단위인 컴포넌트"가 모듈 이름(design.md 2절 표)에만 있다.
- 최소 수정 방향: 초안(편집·플래그), 자료 등록(PHI 포함), 확인, 검수를 별도 객체로 나누고 각각 입력·출력 타입을 둔다. 지금은 설계 문서에 경계를 쓰는 것부터.

### QA-02 (R5, P2) 영역 안 공유 스키마(초안·플래그·source)가 딕셔너리 키 관습뿐이다
- 위치: 정의는 `docs/design.md` 4절 산문뿐. 사용처: `service.py:312-335, 401, 430`, `preview.py:18-25`(`draft["bank"]`, `f["flag_id"]`, `f["resolved"]`), `store/base.py:101`(`d.get("status")`, `d.get("updated_at")`), `store/memory.py:128, 146`, `store/postgres.py:350`(`source_hash`), `store/sqlite.py:197`.
- 인용: `if d.get("status") in ("OPEN", "DISCARDED") and str(d.get("updated_at", "")) < draft_before:` (`base.py:101`)
- 왜 위반인가: 초안·플래그·`source`는 서비스, 미리보기, 저장소(정리), HTTP가 함께 쓰는 영역 내부 스키마인데 JSON Schema도 데이터클래스도 없다. 키 이름이 바뀌면 저장소 정리 같은 먼 곳이 조용히 깨진다. 같은 영역이 책임져야 할 R5의 스키마가 암묵적이다. (대비: 추출기 쪽 `ExtractedFlag`·`ExtractionResult`는 타입이 있다. `base.py:402-413`)
- 최소 수정 방향: `Draft`, `Flag`, `SourceRecord`를 한 모듈의 타입(데이터클래스 또는 JSON Schema)으로 정의하고 저장소·미리보기가 그것만 본다.

### QA-03 (R3, R5, P2) 저장소 한 구현이 불변 버전과 가변 초안·원본을 한 DB에서 서로 엮는다
- 위치: `store/sqlite.py:141-143`(commit_version이 `blobs` 존재를 검사), `store/base.py:96-108`(purge가 drafts, blobs, versions.source를 함께 읽음), `store/postgres.py:281-282, 347-350`.
- 인용: `if sh and not self._db.execute("SELECT 1 FROM blobs WHERE source_hash=?", (sh,)).fetchone():`
- 왜 위반인가: "문항 버전 저장(불변, I1로 나감)"과 "초안·원본 작업 영역(가변)"이 같은 클래스·같은 DB·같은 트랜잭션이다. 버전 쓰기가 원본 테이블의 내부 구조를 알고, 보존 정리는 `versions.source` JSON의 `source_hash` 키(QA-02)를 읽는다. 컴포넌트 사이 DB 스키마 공유(점검 단서 6)가 실제로 있다. 영역 안이라 R1은 아니지만 R3·R5에서 숨은 결합이다. 의도적이며 ADR 0003이 DB 보관의 이유(원자성, 백업)를 적었다. 그 근거는 타당하나, 결합의 모양(어느 테이블이 어느 테이블을 아는지)은 어느 문서에도 스키마로 없다.
- 최소 수정 방향: 테이블 간 의존(`blobs`, `versions.source`, `drafts`)을 한 곳(예: 저장소 스키마 문서/모듈)에 정의하고 `source_hash` 참조를 타입 있는 필드로 한다. 분리까지는 필요 없을 수 있다(결정 사항).

### QA-04 (R3, R4, P2) HTTP 계층이 서비스의 내부(store, v)를 직접 쓴다
- 위치: `http_api.py:112-113`, `:132`, `:135-144`.
- 인용: `r = self.svc.store.reader()` / `v = self.svc.v`
- 왜 위반인가: I1 서버가 `AuthoringService`의 공개 메서드가 아니라 그 안의 저장소와 검증기 필드를 직접 꺼내 쓴다. 저작 서비스 컴포넌트의 내부 상태에 기댄다. 또 `Api` 한 클래스에 인증(`_principal`), I1 서버(`_read`), 저작 라우팅(`_route`), 개선 신호가 같이 있다.
- 최소 수정 방향: I1 서버가 `QuestionReaderView`와 `Validators`를 생성 인자로 받게 한다(서비스를 거치지 않는다).

### QA-05 (R3, R4, P2) 확인과 검수가 `ext["question-authoring"].kind` 관습으로 서로의 출력을 읽는다
- 위치: 쓰기 `service.py:430`(`"kind": "AUTHOR_CONFIRMATION"`), `:476`(`"FOLLOW_UP_REVIEW"`), 읽기 `:469`(`ext.get("kind") == "AUTHOR_CONFIRMATION"`). 역할 분기: `:428`(`clinical_basis_approved: not self.require_two_person`), `:465`.
- 인용: `if ext.get("kind") == "AUTHOR_CONFIRMATION" and old.get("reviewer_id") == actor.id:`
- 왜 위반인가: 검수(add_review)가 확인(confirm)이 남긴 `ext` 안의 비공식 표지에 기대어 판단한다. 두 컴포넌트가 문서화되지 않은 키로 결합돼 있고, 같은 레코드 종류(ReviewRecord)를 `kind` 분기로 두 가지 일에 쓴다. 계약상 `ext`는 "다른 영역은 판단에 쓰지 않는다"이므로 영역 안에서는 괜찮지만 스키마가 없다.
- 최소 수정 방향: `ext` 값의 모양(`kind`, `draft_id`, `preview_hash`, `source`, `acknowledged_flags`, `edited_flags`)을 영역 타입으로 정의한다. 자기 승인 판정은 `reviewer_id` 대조로 충분한지 검토(저자 ID를 `ext`가 아닌 다른 경로로).

### QA-06 (R3, R7, P3) 미리보기가 분류기의 내부 함수·표를 직접 쓰고, 확인이 같은 비교를 두 번 한다
- 위치: `preview.py:109-118`(`classify.diff_banks`, `classify.FOLLOW_UPS`, `KIND_ORDER`, `FOLLOW_UP_ORDER`), `service.py:411`(`classify.diff_banks(base_bank, bank)[0]`)과 `:415`(`classify_change`가 내부에서 다시 `diff_banks`, `classify.py:268`).
- 인용: `kinds, slots, clinical = classify.diff_banks(old, new)` (`preview.py:109`)
- 왜 위반인가: 미리보기가 분류기의 출력(`ChangeClassification`)이 아니라 내부 diff와 상수 표를 가져다 같은 후속 작업 계산(`follow = {...}`)을 다시 구현한다(`preview.py:110` 대 `classify.py:271-273`). 확인은 변경 없음 판정을 위해 diff를 한 번 더 한다. 변경 규칙이 한 곳에서 바뀌면 미리보기 요약과 저장된 분류가 어긋날 수 있다.
- 최소 수정 방향: 분류기가 `classify_preview(old, new)`(종류, 슬롯, 후속 작업)를 한 번 제공하고 미리보기와 확인이 그것만 쓴다.

### QA-07 (R7, P3) 검증을 쓰기 전과 읽는 쪽에서 반복한다
- 위치: `http_api.py:135, 140, 144`(저장소가 내보낸 QuestionBank, ReviewRecord, ChangeClassification을 응답 직전에 다시 스키마 검증), 쓰기 전 검증은 `service.py:406, 422, 437`(confirm), 미리보기 `:349-352`와 확인 `:406`의 QuestionBank 검증 반복, `service.py:306-307`(`_base`를 두 번 계산, 첫 결과 미사용).
- 인용: `bank = r.get_question_bank(ref)      # 해시 재계산 포함` / `v.check("QuestionBank", bank)`
- 왜 위반인가: 이 영역이 쓸 때 검증하고 저장소가 해시로 무결성을 보증한 값을 같은 영역의 I1 서버가 다시 검증한다. 변조 방어라는 의도는 보이지만(저장소 변조 시 `INTEGRITY_ERROR` 시험 존재), R7이 말하는 "내보낸 값은 보증된 값"과 어긋나는 이중 검증이다. 비용 문제(수 MB 문항 세트마다 매 요청)는 측정하지 않았다.
- 최소 수정 방향: 변조 방어를 해시 재계산으로 일원화하고 읽기 경로의 스키마 재검증은 뺄지 결정(ADR로 근거 기록). `ingest_material`의 첫 `_base` 호출은 지운다.

### QA-08 (R1, P2) 빌드 스크립트가 이웃 리포 폴더에 의존한다
- 위치: `scripts/build_wheels.sh:10-13`, Dockerfile `:14-15`(`COPY build/wheels`).
- 인용: `git -C "$here/../survey-service" archive "$SS_TAG" schema | tar -x ...` / `git -C "$here/../boundary-contracts" archive "$BC_TAG" | tar -x ...`
- 왜 위반인가: 이미지를 만들려면 이웃 폴더 `../boundary-contracts`, `../survey-service`가 같은 위치에 git 리포로 있어야 한다. 워크스페이스 규칙은 읽기 전용 참조를 `../survey-service/schema`와 `../boundary-contracts` 파일 읽기로 한정하고, 설치는 `pip install git+ssh://...@<태그>`로 하라고 한다(`requirements.lock`은 그렇게 쓰고 있다). 이웃 폴더 배치와 `git archive` 가능 여부라는 빌드 인터페이스는 계약에 없다. 기본 태그(`v2.1.1`, `survey-schema-v1.0.0`)가 스크립트와 `requirements.lock` 두 곳에 있어 어긋날 수 있다(스크립트 주석이 "같아야 한다"고만 한다). 이 스크립트는 docker 안에서 ssh가 어렵다는 이유로 둔 것이라고 적혀 있다(근거는 환경 제약, 표준 경로가 아니다).
- 최소 수정 방향: 호스트에서 `requirements.lock`으로 wheel을 받아(`pip wheel -r`) 만들고 이웃 폴더를 보지 않는다. 태그는 lock에서 읽는다.

### QA-09 (R1, P2) 문서가 이웃에게 코드 import와 DB 직접 읽기를 안내한다
- 위치: `docs/boundary.md` "제공하는 것" 표와 "이웃 영역의 사용법"(`PostgresQuestionStore(url, read_only=True).reader()`, `MemoryQuestionStore().reader()`), `docs/design.md:9, 169`(읽기 전용 SQLite 연결을 이웃에게 준다), `docs/boundary.md` 장애 영향 표(읽기 전용 SQLite 연결로 계속 읽힌다).
- 인용: "이웃에게는 읽기 전용 뷰(`store.reader()`)와 읽기 전용 SQLite 연결만 준다." (`design.md:9`)
- 왜 위반인가: 워크스페이스 규칙은 영역 사이 호출을 HTTP로 하고 이웃의 저장소 볼륨을 공유하지 않는다(D49), 영역끼리 코드를 가져오지 않는다고 한다. 이 문서는 이웃이 `question_authoring` 패키지를 import하고 DB에 읽기 계정으로 붙는 길을 제공물로 적는다. 구현은 `read_only=True` 옵션(`sqlite.py:55-61`, `postgres.py:79, 129-130`)으로 있다. 이웃이 실제로 이 길을 쓰는지는 이웃 리포를 읽을 수 없어 확인하지 못했다. 쓰고 있으면 P1이다.
- 최소 수정 방향: I1은 HTTP(+계약 클라이언트)와 계약 패키지의 가짜 구현만 제공물로 문서화하고, `read_only` DB 접근과 `question_authoring` import 안내를 제거하거나 "제공하지 않음"으로 바꾼다. 이웃용 메모리 구현이 필요하면 헤드(계약 패키지의 `conformance.servers`)와 조정한다.

### QA-10 (R1, P3) 문서에 이웃 폴더 경로가 있다
- 위치: `README.md:55`, `docs/runbook.md:56`(`bash ../deploy/scripts/dev-postgres.sh`), `README.md:90`(`../head/docs/OPEN_GAPS.md`), README의 `deploy/scripts/lib.sh` 언급.
- 인용: `export TEST_DATABASE_URL="$(bash ../deploy/scripts/dev-postgres.sh up qa)"`
- 왜 위반인가: 허용 목록(`../survey-service/schema`, `../boundary-contracts`, `../head/shared/areas.json`)에 없는 이웃 폴더의 스크립트를 개발 절차에 쓴다. 코드는 아니고 문서 절차이며 시험은 `TEST_DATABASE_URL`만 본다(`tests/conftest.py`).
- 최소 수정 방향: 자체 `compose.dev.yaml`(워크스페이스 규칙이 권하는 방식)로 대체하거나 URL을 직접 주는 절차만 적는다.

### QA-11 (R1, R6, P2) 개선 신호가 비식별 trace의 필드 위치를 가정한다(과거 v1 읽기 잔재는 제거됨)
- 위치: `signals.py:37-39`, 문서 `docs/STATUS.md:18`, 시험 `tests/test_signals.py:75`.
- 인용: `ei = trace.get("extract_input") if isinstance(trace, dict) else None` / `... ei["question_id"]`
- 확인한 것: v1 `planner_output.directive` 읽기는 코드에서 없어졌다(`signals.py:34`가 이유를 적는다. `grep planner_output|directive`는 코드에서 0건, 시험 1건은 "읽지 않는다"를 단언). `extract_input.question_id`는 `../survey-service/schema/schemas/TurnTrace.json`의 `ExtractionInputRecord`에서 필수(`question_id`, minLength 1)다. 문진 스키마는 허용된 의존이다.
- 왜 위반(결합)인가: INTERFACES I7은 `get_trace` 결과가 TurnTrace 스키마 통과를 보장하지 않고(스키마에 없는 키 제거, 정책이 지목한 값 마스킹) 소비자는 "구조 정보(열거값, 식별자, 개수, 순서)만" 쓰라고 한다. 어느 필드가 비식별 뒤에 남는지는 계약에 없다. 이 영역은 `extract_input.question_id`가 남는다고 가정한다. STATUS.md:18은 아직 옛 가정(`planner_output.directive`)을 적고 "ops-feedback과 확인 필요"라고 한다(문서가 코드와 어긋남, P3). ops-feedback 쪽에서 이 필드가 실제로 남는지는 확인하지 못했다(이웃 리포를 읽을 수 없음). 안 남으면 문항 수준 신호가 조용히 비고 `skipped.NO_QUESTION_ID`로만 보인다.
- 최소 수정 방향: 헤드에 변경 요청: 비식별 trace에서 보존이 보장되는 필드(예: `extract_input.question_id`)를 I7에 명시. STATUS.md:18을 코드에 맞게 고친다.

### QA-12 (R5, R6, P3) 로컬 FailureSource Protocol과 출력 형식이 계약 밖에 있다
- 위치: `signals.py:25-27`, 출력 `signals.py:118-124`.
- 인용: `class FailureSource(Protocol): def list_cases(self, *, review_status=None, latest_only: bool = True, cursor=None, limit: int = 100): ...`
- 왜 위반인가: 계약 패키지에 `boundary_contracts.sdk.FailureStore`가 있는데 이 영역이 축소판 Protocol을 따로 정의한다(반환 타입 없음, 튜플 `(items, cursor)` 관습). `/v1/signals` 응답 모양은 코드뿐이며 R5 스키마가 없다(콘솔이 읽는다면 영역 경계를 넘는다).
- 최소 수정 방향: `sdk.FailureStore`를 쓰고 신호 출력을 영역 타입(데이터클래스 또는 JSON Schema)으로 정의한다.

### QA-13 (F, P2) I1 서버가 퍼센트 인코딩된 경로 ID를 디코딩하지 않는다
- 위치: `http_api.py:118`, `:129`(경로 정규식 `[^/]+`), 디코딩 없음(`grep unquote` 0건). 계약: HTTP.md I1 표, CONVENTIONS 10절(`version`·Id는 `[A-Za-z0-9._:-]`), 클라이언트 `clients/_http.py:201-202`(`quote(str(p), safe="")`).
- 인용: `m = re.match(r"^/v1/question-sets/([^/]+)/(versions|latest)$", path)`
- 재현: 메모리 저장소에 ID `qs:a`로 발행한 뒤 `Api.handle("GET", "/v1/question-sets/qs:a/latest")` -> 200, `.../qs%3Aa/latest` -> 404, `.../qs%3Aa/versions/1` -> 404. 공식 클라이언트 `HttpQuestionReader`는 `:`를 `%3A`로 보낸다.
- 왜 위반인가: 서비스는 `:`를 허용하는 ID를 받아(`service.py:30` `_ID`) 저장하지만, 계약 클라이언트로는 읽을 수 없다. 적합성 시험(`tests/test_http_client_conformance.py`)은 `qs-b` 같은 ID만 써서 통과한다. 실제 ID에 `:`를 쓰는 저자가 있으면 이웃이 NOT_FOUND를 받는다.
- 최소 수정 방향: 경로 조각을 정규식 매칭 후 `urllib.parse.unquote`하거나 시험에 `:` 포함 ID를 추가.

### QA-14 (F, P3) 오류 상태 코드 두 가지가 CONVENTIONS 9절 표 밖이다
- 위치: `http_api.py:89`(예상 밖 오류 -> 500 + `UNAVAILABLE`), `:295`(본문 초과 -> 413 + `INVALID_RECORD`).
- 인용: `return 500, BoundaryError("UNAVAILABLE", "내부 오류").to_record()`
- 왜 위반인가: CONVENTIONS 9절은 상태 코드를 "다음으로 고정한다"며 `UNAVAILABLE`은 503이다. 클라이언트는 5xx를 모두 `UNAVAILABLE`로 취급하므로 읽기 쪽 영향은 작다(HTTP.md 공통). 413은 저작 경로(영역 내부)에서만 나온다. `docs/boundary.md`에 500·413이 서술돼 있어 숨은 것은 아니다.
- 최소 수정 방향: 500 -> 503으로 맞추거나 헤드에 표 추가 요청.

### QA-15 (F, P2) CR-0006이 미승인인데 4-eyes 기본이 I1의 관찰 가능한 동작을 바꾼다
- 위치: `service.py:428`(`"clinical_basis_approved": not self.require_two_person`), `docs/change-requests/CR-0006-...md:3`(상태: 제안(헤드 확인 대기)), `docs/brief.md` 3-1·4절("확인은 `ReviewRecord`(`clinical_basis_approved`=true)"), INTERFACES I1("그 확인이 `ReviewRecord`(`clinical_basis_approved`)로 남는다(D30)").
- 인용: `"decision": "APPROVED", "clinical_basis_approved": not self.require_two_person, "reviewed_at": at,`
- 왜 문제인가: 계약 문구(reviewed_only = 가장 최근 검수가 APPROVED이고 true)에는 맞지만, 브리프와 D30이 정한 "저자 확인 = 임상 근거 승인"과 달리 첫 버전이 `reviewed_only`에 나타나는 시점이 검수 역할 승인 뒤로 밀린다. `clinical-reviewer` 토큰·`QA_USER_ROLES`가 배포에 없으면 어떤 버전도 `reviewed_only`에 나오지 않아 이웃(게이트, 합성 데이터)이 NOT_FOUND를 받는다. `design.md` 3.1절 첫 문장(버전은 true와 함께 태어난다)도 코드와 다르다(P3, 문서 불일치). deploy가 검수 역할을 구성하는지 확인하지 못했다.
- 최소 수정 방향: 헤드가 CR-0006을 처리할 때까지 기본값 변경의 영향을 이웃·deploy에 알린다(boundary.md는 이미 적었다). design.md 3.1을 고친다.

### QA-16 (E, R6, P2) 영역 경계를 넘는데 계약에 없는 형식 목록
계약(boundary-contracts)에 스키마·문서가 없는 것:
1. 저작 API 전체(`/v1/drafts`, `/v1/materials`, `/v1/reviews`, `/v1/signals` 등)와 그 요청·응답 모양: HTTP.md가 "영역 내부"라 했으나 사람(저자)·콘솔·gateway가 호출한다. INTERFACES.md 24행은 운영 화면이 I5·I4·I7만 쓴다고 하며 저작 콘솔은 없다. gateway 경유 규약(`Authorization: Bearer <gateway 토큰>` + `X-Openit-User`, 역할은 서비스 쪽 `QA_USER_ROLES`)은 HTTP.md/PUBLIC_API.md의 일반 규약을 따른다고 `boundary.md`가 적었으나 이 서비스의 역할 이름(`question-author`, `clinical-reviewer`)은 계약에 없다.
2. `ReviewRecord.ext["question-authoring"]`의 내용(`kind`, `draft_id`, `preview_hash`, `source{kind, source_hash, filename, size, extractor, extractor_version}`, `acknowledged_flags`, `edited_flags`): 계약은 `source`만 이름 붙이고 모양이 없다(`INTERFACES.md` I1 문단). `ChangeClassification.ext`에는 `draft_id`를 쓴다(`service.py:422`).
3. deploy와의 구성 인터페이스: 환경 변수·비밀 파일 이름 전체(`__main__.py:3-27`), `/readyz` 본문(`status`, `db`, `warnings`), `python -m question_authoring purge`(run-once 잡의 명령, 종료 코드, 출력)와 보존 설정(`QA_DRAFT_RETENTION_DAYS` 등), 이미지 라벨(D50, 워크스페이스 규칙에는 있음). 잡 설정·run-once 인터페이스는 D39가 요구하나 계약 문서에 없다.
4. 빌드 인터페이스: `scripts/build_wheels.sh`의 `build/wheels`, `build/*.tag`(QA-08).
5. 문항 자료 파일 형식(텍스트 양식, `examples/question_form.txt`): 사람 -> 이 영역으로 들어오는 입력이라 이웃 영역 사이 계약은 아니다. 영역 안에서 정의됐고 경계를 넘지 않는다(경계 목록에는 넣되 위반 아님).
6. 변경 분류 4종·후속 작업: 계약 스키마에 있다(`ChangeClassification.schema.json` 열거형). 규칙표(`classify.py` 필드 -> 종류)는 영역 내부이고 결과만 계약으로 나간다. 문제없음.
- 최소 수정 방향: 헤드에 (1) `ext` 의 `source` 모양, (2) 잡 설정·run-once 규약을 계약 또는 deploy 문서로 올리도록 요청.

### QA-17 (E, P2) 브리프의 I8 "규칙 미리보기"는 현행 계약에 해당 함수가 없다
- 위치: `docs/brief.md` 3-1절("받는 것: I8(규칙 미리보기)"), `docs/design.md` 2절 표(미구현), 계약 `INTERFACES.md` I8(2.0.0: `validate_extraction(input, output, flow)`뿐).
- 인용: "받는 것: **I7**(개선 신호), **I4**(판정 조회), **I8**(규칙 미리보기)." (brief.md)
- 왜 문제인가: I8은 모델 출력 의미 검증 라이브러리로 바뀌어 저작용 규칙 미리보기 함수가 없다. I4는 계약이 있으나 이 영역은 소비하지 않는다(미구현). 구현은 없어서 결합은 없지만, 소비 계약이 없는 입력을 문서(README 표 "입력(미구현) turn-engine 규칙 미리보기")가 약속한다.
- 최소 수정 방향: 헤드가 브리프의 I8 문구를 현행 계약에 맞추거나 규칙 미리보기 계약을 새로 정한다.

### QA-18 (R3, P3) 개선 신호가 서비스 쪽 오류 변환을 HTTP 계층에서 한다
- 위치: `http_api.py:191-198`(`build_signals`의 `INTEGRITY_ERROR`, `INVALID_RECORD`, `UNSUPPORTED_VERSION`을 `UNAVAILABLE`로 바꿈), 지연 import `:190`.
- 왜 문제인가: 신호 컴포넌트의 오류 정책이 접착 코드에 있다. 신호 모듈은 이 정책 없이는 "깨진 응답을 성공으로 취급"하지 않는다는 보증을 못 한다. P3(정리 수준).
- 최소 수정 방향: 변환을 `build_signals` 안으로 옮긴다.

### QA-19 (R4, P3) 인증 정보 읽기가 `Api`와 `service._auth`로 갈라져 있다
- 위치: `http_api.py:148-176`(토큰 -> Actor), `service.py:155-165`(역할 -> 허용 여부), `__main__.py:107-121`(토큰 표 파싱, 역할 열거 `ROLES`가 `service.REVIEWER_ROLE`과 별도 상수).
- 인용: `ROLES = ("question-author", "clinical-reviewer")` (`__main__.py:68`) 대 `REVIEWER_ROLE = "clinical-reviewer"` (`service.py:29`)
- 왜 문제인가: 역할 이름이 두 곳에 문자열로 있어 한쪽만 바꾸면 어긋난다. 인증이라는 한 컴포넌트의 스키마(역할 열거)가 한 곳에 없다.
- 최소 수정 방향: 역할 상수를 한 모듈에 두고 `__main__`이 가져다 쓴다.

## 문제없음으로 확인한 것 (방법)

- R1 코드 의존: `grep -rn "import\|from" question_authoring`로 확인한 이웃 import는 `boundary_contracts`(errors, canonical, validator, read_secret, clients.HttpFailureStore, PIPELINE_SCHEMA_VERSION)와 `survey_schema`(`validation.py:14`, `load_schema`)뿐이다. 둘 다 허용된 계약·문진 스키마 패키지다. 이웃 영역 패키지(`ops_feedback`, `turn_engine` 등) import 없음(`grep -rn "turn.engine\|release.gate\|synthetic\|model.lab\|ops_feedback" question_authoring` 0건 중 주석·메시지 제외).
- R1 환경 변수·포트·호스트: 코드의 환경 변수는 모두 `QA_` 접두사의 자기 것이다(`__main__.py`; `grep -rn "os.environ" question_authoring`로 `__main__`뿐임을 확인). 이웃 호스트·포트 하드코딩 없음. 이웃 URL은 `QA_FAILURES_URL`로 받는다.
- 비밀: `read_secret`(`NAME_FILE` 우선)로 읽는다(`__main__.py:58, 108, 113, 126, 135`). 단 `QA_USER_ROLES`는 사용자 ID=역할 표라 환경 변수로 받는다(비밀 값이 아닌 구성으로 판단, 사용자 ID가 비밀인지는 정책 몫).
- Dockerfile: 비root(`USER 10001`), 라벨 키 6개 모두 있음(D50), `/healthz` HEALTHCHECK. 이웃 Dockerfile 인자 없음.
- I1 한 줄씩 대조(HTTP.md I1 표 6행): 경로 6개 일치(`http_api.py:114, 118-128, 129-145`), 목록 `{items, next_cursor}`·limit 기본 100/최대 500(`:101-108`), `latest?reviewed_only=true|false`만 받고 그 밖의 값은 400(`:122-124`), 없으면 404(`store/base.py:166`), `get_change`는 `{"item": ...|null}`(`:145`), 해시 재계산·`INTEGRITY_ERROR` 422(`store/base.py:140-148`, STATUS 표 `:19-20`), 읽기 토큰이 저작 경로를 호출하면 403(`:83`, `_actor`), 토큰 없거나 틀리면 403(CONVENTIONS 9절 (2)), `ref.version == str(bank.version)`(`service.py:408-414`), `affected_slots` = `<question_id>.<slot_id>`(`classify.py:179-181`), `list_versions` 오래된 것부터(`base.py:189`), `get_reviews` 시각순(`base.py:196-198`), `reviewed_only`는 가장 최근 검수 기준(`base.py:153-159`, INTERFACES I1과 일치). 어긋남은 QA-13, QA-14뿐.
- 적합성 시험: `tests/test_http_client_conformance.py`, `test_http_read.py`, `test_reader_conformance.py`, `test_signals.py`를 돌려 71 통과, 23 건너뜀(PostgreSQL). 계약 패키지 `conformance/` 디렉터리의 서버 모음은 이 영역에서 따로 돌리지 않았다.
- I7 소비 대조: 사용 필드 `question_set.id/version`, `review_status`, `failure_type`, `deidentified_trace_ref`는 모두 `FailureCase` 스키마에 있다. `list_cases(review_status, latest_only, cursor, limit)`와 `get_trace(ref)`는 `sdk.FailureStore` 서명과 같다. 클라이언트는 계약의 `HttpFailureStore`를 쓴다(자체 HTTP 호출 없음). 타임아웃 값(5초, 마감 20초)은 영역이 정한 것(ADR 0007·0009)이며 계약에는 값이 없다.
- 변경 분류: `ChangeClassification`을 쓰기 전에 계약 스키마로 검증(`service.py:422`), `change_kinds` 4종·`follow_ups` 3종은 열거형과 일치. 첫 버전은 None.
- 컴포넌트가 영역 밖 데이터를 SDK 밖으로 읽는 곳: 없음(저장소는 자기 DB만).

## 점검하지 못한 것

- 이웃 영역(ops-feedback, release-gate, synthetic-data, model-lab, deploy)의 코드·설정: 읽을 수 없어 QA-09(이웃의 read_only DB·import 사용 여부), QA-11(비식별 trace에 `extract_input.question_id`가 남는지), QA-15(deploy가 clinical-reviewer를 구성하는지)는 확인하지 못했다.
- `store/sqlite.py`, `store/postgres.py`, `store/memory.py`, `extractors/textform.py`, `preview.py` 중반(60-130줄)은 전체를 줄 단위로 읽지 않았다(grep과 일부 발췌). 추출기 내부의 R3·R4(예: 사전 지식, 단위 규칙의 하드코딩)는 점검하지 못했다.
- `tests/` 중 위 4개 파일 외 전부, `scripts/smoke.sh`·`setup-env.sh` 실행(Docker 이미지 빌드, `/healthz`), PostgreSQL 시험, 전체 `pytest`는 돌리지 않았다.
- ADR 12개는 목차만 보고 본문은 대부분 읽지 않았다(ADR 0001·0003·0006·0009 등에서 인용한 근거는 README 요약과 코드 주석에 의존).
- 계약 패키지의 `conformance/servers.py`로 I1 서버를 교차 시험하는 것은 하지 않았다.
- 계약의 타임아웃·재시도 값은 I1·I7에 정해진 것이 없어 대조하지 않았다.
