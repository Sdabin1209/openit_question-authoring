# question-authoring 내부 설계

상태: 2026-10-29 기준(패키지 버전은 pyproject의 0.1.0) 구현 완료 항목은 "구현"으로, 아직 안 한 것은 "미구현"으로 표시한다. 계약 이름과 필드는 `../boundary-contracts`와 `../survey-service/schema`에서 확인한 것만 쓴다. 계약이 말하지 않은 부분을 내가 정한 것은 "제안"으로 적고 `docs/change-requests/`에 근거를 남겼다.

## 1. 원칙

1. 문항 세트 버전은 불변이다. 수정은 새 버전이다(D25).
2. 저자가 사람이 읽는 미리보기로 확인하기 전에는 버전이 되지 않는다(D31). 확인은 `ReviewRecord`로 정확한 버전에 묶인다. 2026-10-28 묶음(옛 표기 0.7.0)부터 기본(4-eyes, D88 U9, ADR 0006)에서는 저자의 확인이 `clinical_basis_approved=false`이고 임상 근거 승인은 다른 주체의 검수 역할이 `add_review`로 한다(이전 D30의 "저자 확인이 곧 임상 승인"은 `QA_REQUIRE_TWO_PERSON=false`일 때의 동작).
3. 저장소에 쓰는 길은 저작 서비스(`service.py`) 하나다. 이웃에게는 읽기 전용 뷰(`store.reader()`)와 읽기 전용 SQLite 연결만 준다.
4. 파싱은 추정하지 않는다. 확신하지 못한 값은 비우거나 원문을 두고 플래그를 달며, 열린 플래그가 있으면 확인할 수 없다.
5. 임상 기준의 내용은 정하지 않는다. 기본값을 만들어 넣지 않는다(정책 수치, 위험 기준, 범위는 자료나 저자가 준 것만 쓴다).

## 2. 컴포넌트 (브리프 시드와의 대응)

| 모듈 | 브리프 시드 | 역할 |
| --- | --- | --- |
| `store/base.py, memory.py, sqlite.py` | 문항 버전 관리자 | 불변 버전·검수·변경 분류 저장, I1 읽기 논리 |
| `validation.py` | 계약·의미 검증기 | 경계 스키마(ReviewRecord 등)와 QuestionBank 검증, 참조 일관성 검사 |
| `classify.py` | 변경 영향 분석기 | ChangeClassification 생성 |
| `extractors/` | 초안 변환기 | 추출기 인터페이스, 레지스트리, 텍스트 양식 추출기 |
| `preview.py` | 저작 콘솔의 미리보기 | 사람이 읽는 미리보기, 변경 요약, 미리보기 해시 |
| `service.py` | 검수 워크플로 | 초안, 편집, 자료 등록, 확인, 이후 검수 |
| `http_api.py`, `__main__.py` | 저작 API | 얇은 HTTP 계층과 진입점(환경 변수는 여기서만 읽는다) |
| 미구현 | 템플릿, 코드·단위 사전, 규칙 미리보기(I8), 판정 조회(I4), 관리자 콘솔 UI | 7절 이후 "미구현" 참고. 개선 신호 수신함(I7)은 구현했다(11-1절) |

## 3. 문항 저장소와 버전 관리 (구현)

- 단위: `(question_set_id, version)`. 버전은 1부터 1씩 늘어나는 정수의 문자열이다. `ArtifactRef = {id: question_set_id, version: "n", content_hash}`.
- `content_hash = boundary_contracts.canonical.sha256_hash(QuestionBank JSON)`. `QuestionBank.version`(정수)은 버전 번호 n과 같게 한다(제안, CR-0002). 그래서 같은 내용이어도 버전이 다르면 해시가 다르다.
- 읽을 때마다 저장된 내용의 해시를 다시 계산해 기록과 다르거나 ref의 해시와 다르면 `INTEGRITY_ERROR`.
- 쓰기(`commit_version`)는 한 트랜잭션으로 버전 + 첫 ReviewRecord + (둘째 버전부터) ChangeClassification을 쓴다. 번호가 마지막+1이 아니면 `CONFLICT`. 같은 `(id, version)`에 같은 해시는 성공(멱등), 다른 해시는 `CONFLICT`. 모든 레코드는 쓰기 전에 스키마로 검증한다.
- 저장 구현은 인터페이스(`AuthoringStore`) 뒤에 있다. 기본은 SQLite 단일 파일(WAL). `versions`, `reviews`, `changes` 테이블은 UPDATE/DELETE를 트리거가 막는다. 초안(`drafts`)과 원본 자료(`blobs`, 해시로 중복 제거)는 가변 작업 영역이며 버전이 아니다. 메모리 구현은 같은 규칙을 따른다.
- 한 서버, 한 프로세스, 관리자만 쓰는 사용자라는 전제로 SQLite의 단일 쓰기로 충분하다고 판단했다(제안). 쓰기 경합이 문제가 되면 `AuthoringStore`를 다른 DB로 바꾼다.
- 다음 번호가 필요한 동시 확인은 `BEGIN IMMEDIATE`로 직렬화되고, 늦은 쪽은 `CONFLICT`가 아니라 서비스 단계에서 "초안을 만든 뒤 새 버전이 생겼다"(`FAILED_PRECONDITION`)로 알린다.

### 3.1 검수와 `reviewed_only`

- 버전은 저자 확인(ReviewRecord, APPROVED, clinical_basis_approved=true)과 함께 태어나므로 모든 버전에는 처음부터 검수가 있다. 이후 `add_review`로 검수를 덧붙일 수 있다(두 번째 검수자, 승인 철회). 이전 검수는 지우지 않는다.
- `latest(reviewed_only=True)`는 "가장 최근 검수(시각, 삽입 순)가 APPROVED이고 clinical_basis_approved가 true"인 가장 큰 버전이다. 계약 문구("APPROVED 검수가 있고 true인")보다 보수적이며, 승인 이후의 반려가 승인을 거두게 하려는 해석이다(제안, CR-0004).

## 4. 도메인 모델 (구현)

초안 `draft`(저장소 내부 레코드. 경계에 노출하지 않는다):

```
draft_id, question_set_id, status: OPEN | CONFIRMED | DISCARDED
base: ArtifactRef | None        # 이 초안이 출발한 버전
bank: QuestionBank 모양(version 없음, 불완전할 수 있음)
flags: [{flag_id, path(JSON 포인터), kind: MISSING|UNSURE|UNPARSED, message, raw, resolved, resolution: EDITED|ACKNOWLEDGED}]
source: {kind: material, source_hash, filename, size, extractor, extractor_version} | None(화면 입력)
fallback: {reason, guidance} | None
warnings: [환자 정보 경고]
confirming: {version, at}       # 확인 중 예약한 번호와 시각(재시도 멱등용)
confirmed: {ref, preview_hash, review_id}
```

## 5. 확인 워크플로 (구현)

```
(자료 올리기 | 화면 입력) -> 초안(OPEN) -> [편집/플래그 확인]* -> 미리보기 -> confirm(preview_hash) -> 버전 + ReviewRecord
                                  \-> discard (버전 아님, 기록은 초안에 사유로 남음)
```

1. 초안 만들기: `create_draft`(기본은 최신 버전에서 복사, 새 문항 세트면 빈 초안) 또는 `ingest_material`.
2. 편집: `apply_edits`는 JSON 포인터 기반 `set/append/remove`다. 이것이 화면 입력(템플릿 양식)의 저장 형태이며, 콘솔이 필드 입력창을 이 호출로 옮긴다. 편집한 경로(와 그 아래)의 플래그는 "저자가 직접 정함"(EDITED)으로 닫힌다. 추출된 값이 맞으면 `acknowledge_flags`로 닫는다(ACKNOWLEDGED). 둘 다 기록에 남는다.
3. 미리보기(`get_preview`): 사람이 읽는 한국어 글(`text`), 열린 플래그, 스키마·참조 검증 오류, 기반 버전과의 변경 요약, `preview_hash`, `ready_to_confirm`. 수치(정책 한도, 단위·범위·환산, 위험 기준, 세션 한도)는 "수치" 줄로 항상 따로 보인다. 비어 있는 값은 "(비어 있음)"으로 보이고 채우지 않는다.
4. 확인(`confirm`): 저자 권한 확인 -> 미리보기 해시가 현재 초안과 같은지 -> 열린 플래그 0 -> 기반이 아직 최신인지 -> `version` 부여 후 QuestionBank 스키마와 참조 검증 -> 이전 버전과 내용이 같지 않은지 -> ChangeClassification 생성 -> ReviewRecord(`reviewer_role`=확인한 사람의 역할, `reviewer_id`, APPROVED, `clinical_basis_approved`=4-eyes면 false 아니면 true) 생성 -> 한 트랜잭션으로 저장. 어느 단계든 실패하면 아무것도 저장되지 않는다. 같은 `preview_hash`로 다시 부르면 같은 ref(멱등), 다른 해시면 `CONFLICT`. 저장 직후 초안 갱신에 실패해도 예약된 번호와 시각으로 재시도하면 같은 버전을 돌려준다(시험함).
5. ReviewRecord의 `ext.question-authoring`: `kind`, `draft_id`, `preview_hash`, `source`(자료 해시 포함), 직접 고친 플래그와 확인한 플래그의 ID.
6. 역할: 서비스는 허용 역할 집합(`author_roles`)을 받는다. 어떤 역할 이름을 쓸지는 운영이 정한다(기본값 `question-author`는 제안). 임상 승인을 하는 저자와 콘솔 사용자의 구분은 미구현이며 인증은 11절.

## 6. ChangeClassification (구현)

이전 버전과 새 버전의 QuestionBank를 필드 단위로 비교한다. 첫 버전은 변경 분류가 없다(`get_change`는 None). `from`은 직전 버전 ref.

| 바뀐 것 | 종류 | 영향 슬롯 | 임상 검토 표시 |
| --- | --- | --- | --- |
| 문항 name, question_sentence, refusal_explanation, guide_sentences | TEXT | 그 문항의 모든 슬롯 | 아니오 |
| 슬롯 reask_sentence | TEXT | 그 슬롯 | 아니오 |
| 세션 fixed_sentences, 안내 목록의 문장만 | TEXT | 없음 | 아니오 |
| 세션 risk_phrases | TEXT | 없음 | 예 |
| 슬롯 units(범위·환산 포함) | CHOICE_UNIT | 그 슬롯 | 예 |
| 슬롯 choices, choice_catalog, yes_no_map | CHOICE_UNIT | 그 슬롯 | 아니오 |
| 슬롯 required | SLOT | 그 슬롯 | 아니오 |
| 슬롯 risk_rule | SLOT | 그 슬롯 | 예 |
| 슬롯 추가·삭제 | SLOT | 그 슬롯 | 아니오 |
| 문항 order, prerequisite, affected_followups, repeat_keys, policy, 문항 목록 순서 | BRANCH | 그 문항의 모든 슬롯(순서 변경은 없음) | 아니오 |
| 문항 추가·삭제 | SLOT + BRANCH | 그 문항의 슬롯 | 아니오 |
| 세션 confirm_limit 등 한도, 안내 목록의 기준·단위·채움값 | BRANCH | 없음 | 아니오 |
| 위 표에 없는 필드 | SLOT + BRANCH | 해당 | 예 |

- `affected_slots`는 `"<question_id>.<slot_id>"` 문자열(제안, CR-0003).
- 후속 작업: TEXT -> REGENERATE_SAMPLES. CHOICE_UNIT, SLOT, BRANCH -> REGENERATE_SAMPLES + REEVALUATE_MODEL. 임상 검토 표시가 있으면 CLINICAL_REVIEW 추가(제안, CR-0004). "임상 검토 표시"는 값이 옳은지 판단하는 것이 아니라, 저자가 한 번 더 볼 만한 영역(수치·위험 기준)이 바뀌었다는 기계적 표시다.
- 표가 스키마의 모든 필드를 덮는지는 시험(`test_tables_cover_schema_fields`)이 확인한다. 스키마에 필드가 늘면 이 시험이 먼저 깨진다.
- 이전 버전과 내용이 같으면(버전 번호만 다르면) 새 버전을 만들지 않는다(`FAILED_PRECONDITION`).

## 7. 문항 자료 등록 파이프라인 (구현)

```
bytes + filename
  -> 환자 정보 패턴 검사(경고, 저자가 accept_phi_warning을 주어야 계속)
  -> 원본 해시 = canonical.file_hash, 원본 보관(blobs)
  -> 레지스트리가 추출기를 고른다(이름 지정 또는 can_handle)
  -> 추출: ExtractionResult(bank, flags)  | ExtractionFailure -> FALLBACK 초안(화면 입력으로 이어 감)
  -> 초안(OPEN) + 플래그 -> 미리보기 -> 확인 (5절과 같은 절차)
```

- 추출기 인터페이스(`extractors/base.py`): `name`, `version`, `extensions`, `can_handle(filename, data)`, `extract(data, filename) -> ExtractionResult`. 새 형식은 클래스 하나를 `ExtractorRegistry.register`로 붙이면 된다(시험함). 어떤 추출기든 결과는 같은 초안·미리보기·확인을 거친다. 추출기 예외도 등록을 막지 않고 FALLBACK이 된다.
- FALLBACK: 파싱 실패(지원하지 않는 형식, 인코딩, 문항이 하나도 없음)면 사유와 안내가 붙은 빈 초안(또는 기반 버전 복사본)을 돌려주고 화면 입력으로 같은 절차를 따라 등록한다. 실패한 원본의 해시도 출처로 남는다. 그래서 등록은 항상 가능하다(D33).
- 출처 기록: ReviewRecord의 `ext.question-authoring.source`에 `{source_hash, filename, size, extractor, extractor_version}`(화면 입력이면 `{kind: screen}`). 계약 변경은 없다. `QuestionBank`는 `additionalProperties: false`라서 문항 세트 안에는 둘 수 없었고, 첫 버전에는 ChangeClassification이 없으므로 모든 버전에 있는 ReviewRecord를 택했다(CR-0001).
- 환자 정보 경고: 주민등록번호, 전화번호, 이메일 모양의 단순 패턴. 경고일 뿐 판단은 저자가 한다. 경고를 받아들이지 않으면 자료를 저장하지 않는다.

### 7.1 첫 추출기: 텍스트 양식(`text-form`, .txt/.md)

선택 이유(제안): (a) 규칙 기반이라 결정적이고 시험할 수 있으며 모델이나 외부 라이브러리가 필요 없다. (b) 의료인이 워드·PDF에서 복사해 붙이거나 직접 쓸 수 있다. (c) 줄 문법이라 "어느 줄을 해석하지 못했는지"를 정확히 알려 줄 수 있어 조용한 추정이 없다. (d) 나중에 PDF·Word 추출기는 텍스트 추출까지만 따로 하고 이 문법 파서를 재사용할 수 있다. 실제 저자가 어떤 형식을 가장 많이 쓰는지는 확인하지 못했다. 이것은 첫 형식일 뿐이다.

문법(줄 단위, `#`로 시작하는 줄과 빈 줄은 무시, 들여쓰기는 의미 없음, 항목 이름은 첫 `:` 앞):

```
세션 설정
위험 문구: 문구1, 문구2        (또는 '없음')
확인 한도: N / 인식 실패 연속 한도: N / 질문 횟수 한도: N
고정 문장 <KEY>: 문장
안내 <ID>: 기준 | 문항별|전체 | 문장|없음 | REMAINING_COUNT|없음
문항 <ID>: <이름>
순서: N / 선행 조건: 없음 | <문항>.<슬롯> = <값>
질문: ... / 거절 설명: ... / 안내 문장 <ID>: ... / 후속 문항: a, b / 반복 키: a, b
정책: 질문 한도 N, 모름 N, 거절 N
슬롯 <ID>: 필수|선택
다시 묻기: ...
단위: <이름> | 같은 말: a, b | 범위 최소 ~ 최대 | 환산: <배수> <대상 단위>   (여러 줄 가능)
선택지: a, b / 선택지 코드 <코드>: 라벨 | 같은 말: a, b
예아니오: 예=<값>, 아니오=<값> | 없음
위험 기준: (>= | =) <값> <단위> (즉시|결과 시) | 없음
```

확신하지 못한 것의 처리(시험함):
- 정해진 모양에 정확히 맞지 않는 범위, 환산, 위험 기준, 정책, 순서, 선행 조건은 값을 비우고 UNSURE 플래그에 원문을 담는다(예: `34-42`, `> 40 도 즉시`, 단위 없는 수치).
- 위험 기준의 단위가 그 슬롯의 단위 목록에 없거나 값이 선택지에 없으면 값은 읽되 UNSURE. 단위 줄은 위험 기준 줄보다 앞에 있어야 한다.
- 자료에 없는 필수 항목(정책, 다시 묻기, 위험 기준 등)은 MISSING. "없음"이라고 쓴 것만 null로 읽는다. 해석하지 못한 줄은 버리지 않고 UNPARSED로 원문과 함께 알린다.
- 수치가 문법에 맞게 읽혔더라도 미리보기에서는 항상 "수치" 줄로 보인다. 열린 플래그가 없다고 해서 저자가 읽지 않아도 되는 것이 아니다.

## 8. 저작 API (핵심 로직 구현, HTTP 얇게 구현)

요청은 계약 `AuthoringRequests`(모르는 키 거부), 응답은 `AuthoringResponses`·`SignalsResponse`로 검사한다(`question_authoring/contract.py`). 왜: 오타 난 필드가 조용히 무시되어 저자의 입력이 저장되지 않는 일을 막고, 서버가 키를 늘리면 시험이 먼저 깨지게 한다(계약 스키마 설명, D107).

| 메서드 | 경로 | 서비스 호출 |
| --- | --- | --- |
| POST | /v1/drafts | create_draft |
| POST | /v1/materials?question_set_id&filename[&base_version&extractor&accept_phi_warning] (본문=원본 바이트) | ingest_material |
| GET | /v1/drafts, /v1/drafts/{id}, /v1/drafts/{id}/preview | list/get_draft, get_preview |
| POST | /v1/drafts/{id}/edits, /acknowledge, /confirm, /discard | apply_edits, acknowledge_flags, confirm, discard_draft |
| POST | /v1/reviews | add_review |
| GET | /healthz, /readyz | 살아 있음, 저장소 준비됨 |

오류는 `BoundaryError` 레코드(JSON)이고 HTTP 상태는 코드에서 정한다(NOT_FOUND 404, INVALID_RECORD 400, CONFLICT 409, FAILED_PRECONDITION 412, PERMISSION_DENIED 401(자격 없음·틀림, `WWW-Authenticate: Bearer`) 또는 403(권한 없음, CONVENTIONS 6절), UNAVAILABLE 503). 콘솔은 이 API만 쓰며 저장소에 직접 쓰지 않는다.

## 9. I1 구현과 적합성 (구현)

- `AuthoringStore` 기반 클래스가 `QuestionReader` 다섯 메서드를 구현하고(읽기 논리 한 곳), `MemoryQuestionStore`와 `SqliteQuestionStore`가 원시 연산만 구현한다. `store.reader()`는 읽기만 있는 뷰다. `isinstance(x, QuestionReader)`를 통과한다(`runtime_checkable`).
- 적합성 시험(`tests/test_reader_conformance.py`)은 메모리, SQLite 메모리 DB, SQLite 파일 세 구현에 같은 시험을 돌린다: 해시 검증, NOT_FOUND/INTEGRITY_ERROR/INVALID_RECORD, 페이지 순서와 커서, `reviewed_only` 의미, 복사본 반환, 변조 감지, 멱등과 CONFLICT.
- 페이지: `list_versions`의 `cursor`는 마지막 버전 번호, `list_ids`의 `cursor`는 마지막 문항 세트 ID(ID 오름차순), `limit` 상한 1000.

## 10. 오류와 멱등 요약

모든 경계 호출의 실패는 `BoundaryError`. 레코드는 쓰기 전에 스키마 검증(`INVALID_RECORD`). 같은 키·같은 내용은 성공, 같은 키·다른 내용은 `CONFLICT`(CONVENTIONS 4절). 읽기 중 SQLite 오류는 `UNAVAILABLE`(재시도 가능).

## 11. 보안·개인정보

- 인증: 정적 토큰 -> (actor id, 역할) 표를 환경 변수 `QA_AUTHOR_TOKENS`로 받는다(제안. 저자 인증 방식은 열린 문제이고 경계에 닿으면 변경 요청을 낸다). 토큰은 이미지에 넣지 않는다.
- 업로드 자료에 환자 정보는 없다고 가정하되 저장 전에 경고한다(7절).
- 읽기 전용 SQLite 연결(`read_only=True`)로 이웃 영역에 읽기 계정을 줄 수 있다.

## 11-1. 개선 신호 수신함 (구현, 읽기 전용)

- 입력: ops-feedback의 I7(`HttpFailureStore`). `list_cases(latest_only=True)`로 실패 사례를 쪽별로 읽고(상한 `max_cases`, 기본 2000, 넘으면 `truncated`) `get_trace`로 비식별 trace를 읽는다. I7 클라이언트는 서비스 진입점이 `QA_FAILURES_URL`과 `QA_FAILURES_TOKEN_ENV`(토큰이 든 환경 변수의 이름)로 만든다. 미설정이면 `GET /v1/signals`는 `{"status": "DISABLED", "reason": ...}`를 낸다.
- 출력(`signals.py`, `GET /v1/signals?question_set_id=&review_status=&max_cases=`, 저자 인증): 문항 세트별 (a) 사례 수, 유형 분포, 버전별 수, (c) CONFIRMED 수. 문항별로 같은 수와 분포, (b) 혼동(같은 문항에서 같은 유형이 `confusion_min`=2회 이상). 문항 세트는 `FailureCase.question_set.id`, 문항은 비식별 TurnTrace의 `extract_input.question_id`에서 읽는다(TurnTrace.json의 필수 필드).
- 건너뛰기: 사례에 `question_set`이 없으면 `NO_QUESTION_SET`, trace를 읽지 못하면 `TRACE_UNREADABLE`, trace에 문항 ID가 없으면 `NO_QUESTION_ID`(문항 세트 수준에는 계속 센다). 추정하지 않고 건수를 `skipped`에 낸다.
- 하지 않는 것: 자동 수정, 발화·trace 본문 저장이나 반환(O19 미정). 건수와 ID만 낸다. 신호는 읽을 때마다 계산하며 저장하지 않는다. I7 실패는 `BoundaryError`(PERMISSION_DENIED, UNAVAILABLE 등)로 그대로 전해진다.

## 12. 범위 밖 또는 미구현

- 관리자 콘솔 UI(저작 API의 클라이언트로 나중에).
- 소비하는 계약: I7 개선 신호 수신함은 11-1절에 구현했다. I4 판정 조회, I8 규칙 미리보기는 미구현. 미리보기의 변경 요약은 규칙 실행이 아니라 구조 비교다.
- 템플릿과 코드·단위 사전: 화면 입력용 필드 목록과 사전은 콘솔을 만들 때 설계한다. 사전의 내용(코드 목록)은 저자가 정한다.
- PDF·Word 추출기.
- 저자 인증의 실제 방식, 저자별 권한 구분.
