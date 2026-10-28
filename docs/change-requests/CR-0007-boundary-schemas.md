# CR-0007 영역 경계를 넘는 형식의 스키마 초안 (지시서 1 B)

- 상태: 제안(헤드 확인 대기). 코드는 바꾸지 않았다. 스키마를 계약에 올린 뒤에 이 영역이 쓰도록 바꾼다(다음 단계).
- 근거: `docs/audit/structure-rules-2026-10-28.md` QA-16(E절 목록), QA-12.
- 파일: `docs/change-requests/schemas/*.schema.json`(JSON Schema 2020-12), 예시 `schemas/examples/*.json`, 검증 스크립트 `schemas/check.py`.
- 예시는 실제 코드가 낸 출력이다(메모리 저장소 서비스를 `Api.handle`로 호출해 저장. 환경 변수 예시만 `__main__.py` 머리말의 형식을 따라 손으로 썼다).

## 검증

```
bash scripts/setup-env.sh
.venv/bin/python docs/change-requests/schemas/check.py
```

출력의 마지막 줄: `예시 45개, 기대와 다른 것 0개`. 통과해야 할 예시(`*.valid*.json`)는 통과하고 거부되어야 할 예시(`*.invalid.json`, 형식마다 1개)는 거부된다. 거부 사유 줄은 실행하면 나온다(예: `AddReviewRequest.invalid.json 거부: 'false' is not of type 'boolean'`). 스키마 자체도 `Draft202012Validator.check_schema`로 검사한다.

## 형식 목록

| 스키마 | 만드는 쪽 -> 읽는 쪽 | 포함하는 $defs |
| --- | --- | --- |
| `authoring-requests.schema.json` | 저자·검수자의 콘솔/gateway -> question-authoring | CreateDraftRequest, MaterialQuery, EditsRequest(Edit), AcknowledgeRequest, ConfirmRequest, DiscardRequest, AddReviewRequest, SignalsQuery |
| `authoring-responses.schema.json` | question-authoring -> 콘솔/gateway | MaterialIngested, Draft(Flag, Source), DraftList, Preview, ConfirmResponse, AddReviewResponse |
| `signals-response.schema.json` | question-authoring -> 저자 콘솔 (`GET /v1/signals`) | DISABLED / OK 두 모양 |
| `ext-question-authoring.schema.json` | question-authoring -> 게이트·합성 데이터·콘솔 (`ReviewRecord.ext`, `ChangeClassification.ext`의 `"question-authoring"` 값) | ReviewExt(AUTHOR_CONFIRMATION, FOLLOW_UP_REVIEW), ChangeExt |
| `deploy-interface.schema.json` | deploy <-> question-authoring | Environment(환경 변수), ReadyzResponse, PurgeResult |

점검 보고서 QA-16의 4번(`scripts/build_wheels.sh` 산출물)은 이 스키마 초안에 넣지 않았다. 5번(문항 자료 텍스트 양식)은 사람 -> 이 영역 입력이고 영역 안에서 정의되므로 넣지 않았다. 둘 다 "하지 않은 것"이다.

## 형식마다

### 저작 API 요청 (authoring-requests)
- 예시: `examples/CreateDraftRequest.valid.json`, `MaterialQuery.valid.json`, `EditsRequest.valid.json`, `AcknowledgeRequest.valid.json`, `ConfirmRequest.valid.json`, `DiscardRequest.valid.json`, `AddReviewRequest.valid.json`, `SignalsQuery.valid.json`. 거부 예: `base_versoin`(오타 키), `accept_phi_warning: "yes"`, `op: "replace"`, `clinical_basis_approved: "false"`(문자열) 등.
- 모르는 키 정책: **거부**. 오타 난 필드가 조용히 무시되면 저자가 보낸 값이 저장되지 않는데도 성공으로 보이기 때문이다. (현재 코드는 `_need`로 필수만 보고 모르는 키는 무시한다. 스키마가 올라가면 거부로 바뀐다. 이 차이는 코드를 바꿀 때 시험으로 확인할 사항이다.)
- 불변식: `clinical_basis_approved`는 불리언만. `preview_hash`는 `sha256:` 64자리 16진. 본문은 8MiB 이하(초과는 400).

### 저작 API 응답 (authoring-responses)
- 예시: `Draft.valid.json`(플래그 1건이 열린 초안), `Draft.confirmed.valid.json`, `Draft.discarded.valid.json`, `DraftList.valid.json`, `Preview.valid.json`, `MaterialIngested.valid.json`, `MaterialIngested.fallback.valid.json`, `ConfirmResponse.valid.json`, `AddReviewResponse.valid.json`.
- 모르는 키 정책: 스키마는 서버가 내는 키를 모두 열거해 **닫는다**(`additionalProperties:false`). 서버가 키를 늘리면 시험이 먼저 깨져 스키마 갱신을 강제한다. 클라이언트(읽는 쪽)는 모르는 키를 무시해도 된다. `Draft.bank`는 문진 스키마 QuestionBank의 초안 모양이어서 `object`로만 본다(확정 때 QuestionBank 스키마로 검증된다). `AddReviewResponse`는 계약 `ReviewRecord`이므로 계약 스키마로 검증하는 것이 정본이고 여기서는 필수 키 확인에 그친다.
- 불변식: 확정된 버전의 `Ref.version == str(QuestionBank.version)`. 같은 `preview_hash`로 `confirm`을 다시 부르면 같은 `Ref`(멱등). 플래그가 열려 있으면 `ready_to_confirm=false`. 초안은 `OPEN`에서만 고쳐진다.
- 주의(제안): `Preview.change`와 `Draft.fallback`의 세부는 현재 코드의 모양을 그대로 적었다. 오류 응답은 계약 `BoundaryError`(여기에 없음).

### 개선 신호 `/v1/signals` (signals-response)
- 예시: `signals.ok.valid.json`(`build_signals`에 가짜 FailureStore를 물린 실제 출력), `signals.disabled.valid.json`(`QA_FAILURES_URL` 미설정 때 실제 응답). 거부 예: 정의에 없는 키 `extra`.
- 모르는 키 정책: 거부(닫음). 발화·trace 본문이 응답에 새어 들어오는 변경을 스키마가 잡게 하려는 것이다.
- 불변식: 건수와 유형 분포와 ID만 담는다(발화 문장, trace 본문 없음). `confirmed_count <= case_count`. 신호는 읽기 전용이며 아무것도 고치지 않는다. 상류(I7) 깨진 응답은 성공이 아니라 `UNAVAILABLE`(503)이다. QA-12는 별도로 로컬 `FailureSource` Protocol을 계약의 `sdk.FailureStore`로 바꾸는 것을 제안했다(이 CR은 응답 모양만 다룬다).

### `ReviewRecord.ext["question-authoring"]` / `ChangeClassification.ext` (ext-question-authoring)
- 예시: `ReviewExt.author.valid.json`(AUTHOR_CONFIRMATION, `source` 포함), `ReviewExt.followup.valid.json`(FOLLOW_UP_REVIEW), `ChangeExt.valid.json`. 거부 예: `source`·`preview_hash` 등이 빠진 AUTHOR_CONFIRMATION, 정의에 없는 키가 붙은 ChangeExt.
- 모르는 키 정책: **거부**. 이 값은 "누가 무엇을 보고 확인했는가"의 출처 기록이므로 조용히 늘어난 키를 허용하지 않고, 키가 늘면 스키마를 먼저 올린다. 읽는 쪽 영역은 계약 규칙대로 이해하지 못해도 보존하고 판단에 쓰지 않는다.
- 불변식: `source.kind`는 `screen` 또는 `material`이고 `material`이면 `source_hash`, `filename`, `size`가 있다. `kind=AUTHOR_CONFIRMATION`인 검수 하나가 모든 버전에 있다. `acknowledged_flags`와 `edited_flags`는 겹치지 않는다(코드가 한 플래그에 `resolution` 하나만 기록, 스키마로는 표현하지 않았다).
- 헤드에 묻는 것: 이 스키마를 계약의 `ReviewRecord`에 `ext` 값 모양으로 올릴지, 이 영역의 문서에 두고 계약은 `source` 이름만 둘지.

### deploy 구성 인터페이스 (deploy-interface)
- 예시: `Environment.valid.json`, `ReadyzResponse.valid.json`(`warnings:["two_person_disabled"]`가 있는 실제 응답), `PurgeResult.valid.json`(`purge_once`의 실제 반환). 거부 예: `QA_DRAFT_RETENTION_DAYS=0`, 역할 `admin`, 정의에 없는 `/readyz` status, 음수 건수.
- 모르는 키 정책: 환경 변수는 **무시**한다. 환경에는 이 서비스와 무관한 변수가 항상 섞여 있어 거부할 수 없다. 아는 변수의 형식이 틀리면 기동을 거부한다(코드가 이미 그렇게 한다). `/readyz`·`purge` 출력은 닫는다.
- 비밀: `QA_DATABASE_URL`, `QA_AUTHOR_TOKENS`, `QA_READ_TOKENS`, `QA_GATEWAY_TOKENS`, `QA_FAILURES_TOKEN`은 `<NAME>_FILE`로 받는다(D84). 값은 `/readyz`, 로그, 오류에 쓰지 않는다. `QA_USER_ROLES`는 사용자 ID와 역할표여서 비밀이 아닌 구성으로 판단했다(점검 보고서의 "문제없음"에서도 같은 판단, 정책 확인 필요).
- `purge`: `python -m question_authoring purge`. 종료 코드 0은 성공(0건 포함, 멱등), 0이 아니면 구성 오류. 보존 일수는 양의 정수만(무기한 불가). **제안**: 지금 출력은 파이썬 dict 표기(`{'drafts_removed': 0, ...}`)로 JSON이 아니다. 스키마는 JSON을 가정하므로 코드를 바꿀 때 `json.dumps`로 낸다(지금 코드는 바꾸지 않았다). 실행 주기는 deploy가 정한다(D39).
- 이미지 라벨(D50)은 워크스페이스 규칙에 있어 여기에 다시 쓰지 않았다.

## 하지 않은 것
- 스키마를 계약에 올리는 일(헤드), 영역 코드가 이 스키마를 쓰게 바꾸는 일.
- 역할 이름(`question-author`, `clinical-reviewer`)을 계약 열거형으로 올리는 제안은 deploy-interface의 정규식에 적었을 뿐 별도 제안은 아니다.
