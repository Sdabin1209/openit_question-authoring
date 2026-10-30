# 컴포넌트 (D117, 2026-10-30)

영역의 일을 업무 단계로 나눈 결과다. 입력은 계약(boundary-contracts v3.1.1)과 [브리프](brief.md)이고, 기존 코드는 빠진 업무 규칙·거부 조건을 찾는 대조에만 썼다. 컴포넌트는 "입력 스키마 하나 -> 출력 스키마 하나"인 함수 모양이고(저장소·통신은 바깥 자원을 다루는 컴포넌트), 서로 import하지 않는다. 연결은 `question_authoring/wiring/`이 한다. 스키마 파일은 `schemas/<이름>.json`, 계약 타입은 `파일#$defs이름`으로 표시한다. 스키마 밖 조건은 [schemas/README.md](../schemas/README.md).

## 업무 흐름

자료 올리기: `phi_screen` -> (저장소에 원본) -> `draft_open` -> `extract_textform` -> `draft_ingest` -> (저장)
미리보기: `bank_validate` -> `change_diff` -> `preview_hash` -> `preview_build`
확인: `access_policy` -> `preview_hash` -> `confirm_plan` -> `bank_validate` -> `change_diff` -> `change_record` -> `confirm_finalize` -> (저장소 commit)
편집·확인·버리기: `access_policy` -> `draft_edit` | `flag_acknowledge` | `draft_discard` -> (저장)
이후 검수: `access_policy` -> `review_followup` -> (저장소)
개선 신호: `link_failures_read` -> `signals_build`
읽기(I1): `link_authoring_server` -> `question_store`(백엔드 주입)

## 표

| 컴포넌트 | 파일 | 입력 타입 | 출력 타입 | 하는 일 |
|---|---|---|---|---|
| 접근 정책 | `components/access_policy.py` | AccessCheckInput | AccessCheckResult | 행위자 역할이 허용되는지, 초안 주인이 맞는지(4-eyes) 판단 |
| 환자 정보 경고 | `components/phi_screen.py` | PhiScreenInput | PhiScreenResult | 자료에서 환자 정보 모양을 찾아 경고. 경고가 있고 저자가 받아들이지 않았으면 거절 |
| 텍스트 양식 추출 | `components/extract_textform.py` | MaterialExtractRequest | MaterialExtraction | 텍스트 양식을 문항 세트 초안과 확인 플래그로 바꾼다. 못 읽으면 FAILED와 사유 |
| 문항 세트 검증 | `components/bank_validate.py` | BankValidateInput | BankValidateResult | 문진 스키마 QuestionBank 검사와 참조 무결성 |
| 초안 열기 | `components/draft_open.py` | DraftOpenInput | AuthoringResponses#Draft | 기반 버전(또는 빈 세트)에서 가변 초안 생성 |
| 자료 반영 | `components/draft_ingest.py` | DraftIngestInput | DraftIngestResult | 추출 결과(또는 읽지 못한 사유)를 초안에 싣고 플래그 ID를 매긴다 |
| 초안 편집 | `components/draft_edit.py` | DraftEditInput | AuthoringResponses#Draft | JSON 포인터 편집 적용, 고친 경로의 플래그를 닫는다 |
| 플래그 확인 | `components/flag_acknowledge.py` | FlagAcknowledgeInput | AuthoringResponses#Draft | 저자가 추출값이 맞다고 확인 |
| 초안 버리기 | `components/draft_discard.py` | DraftDiscardInput | AuthoringResponses#Draft | 확인 없이 버린다(ReviewRecord 없음) |
| 변경 비교 | `components/change_diff.py` | ChangeDiffInput | ChangeDiff | 이전·새 문항 세트를 비교해 변경 종류 4종, 영향 슬롯, 후속 작업 |
| 변경 분류 레코드 | `components/change_record.py` | ChangeRecordInput | ChangeRecord(계약 ChangeClassification + ChangeExt) | 비교 결과와 새 ref로 계약 레코드를 만든다 |
| 미리보기 해시 | `components/preview_hash.py` | PreviewHashInput | PreviewHashResult | 저자가 본 것과 확인하는 것이 같음을 묶는 해시 |
| 미리보기 | `components/preview_build.py` | PreviewBuildInput | AuthoringResponses#Preview | 사람이 읽는 미리보기(확인 필요 표시) |
| 확인 계획 | `components/confirm_plan.py` | ConfirmPlanInput | ConfirmPlan | 다시 응답(멱등)·새 버전·거절 판단, 번호와 시각 예약 |
| 확인 마무리 | `components/confirm_finalize.py` | ConfirmFinalizeInput | ConfirmFinalizeResult(계약 ReviewRecord + ReviewExt) | 검증 실패·내용 같음을 거절하고 저자 확인 레코드를 만든다 |
| 이후 검수 | `components/review_followup.py` | ReviewFollowupInput | ReviewFollowupResult | 검수 덧붙이기 규칙(임상 승인은 검수 역할, 자기 승인 금지, 멱등) |
| 개선 신호 | `components/signals_build.py` | SignalsBuildInput | SignalsResponse(계약) | 읽어 온 사례·trace를 문항별 건수로 센다 |
| 문항 저장소 | `components/question_store.py` | StoreRefQuery, StorePageQuery, StoreVersionPageQuery(읽기 요청) | 계약 ArtifactRef·QuestionBank·ReviewRecord·ChangeClassification | I1 읽기 논리, 보존·정리(purge). 백엔드를 주입받는다 |
| 저장소 백엔드(메모리) | `components/store_memory.py` | 원시 연산(버전·검수·변경·초안·원본) | 같음 | 메모리 구현(가짜). 이웃이 혼자 시험하는 데 쓴다 |
| 저장소 백엔드(SQLite) | `components/store_sqlite.py` | 같음 | 같음 | 개발·시험용 파일 DB. 버전·검수·변경 표는 삽입만(D25) |
| 저장소 백엔드(PostgreSQL) | `components/store_postgres.py` | 같음 | 같음 | 운영 DB(ADR 0017) |
| 통신: 실패 사례 읽기 | `components/link_failures_read.py` | FailureReadRequest | FailureBundle | ops-feedback(I7)에서 사례와 비식별 trace를 읽는다(보내는 쪽) |
| 통신: 저작 API 서버 | `components/link_authoring_server.py` | 계약 AuthoringRequests | 계약 AuthoringResponses·SignalsResponse | openit_link 위에서 HTTP 경로를 받아 service에 넘긴다(받는 쪽) |

진입점·설정·마이그레이션은 컴포넌트가 아니다: `__main__.py`(환경 변수 읽기, 기동, purge 잡), `store/migrate.py`, `migrations/`.

## 연결(wiring)

| 파일 | 하는 일 |
|---|---|
| `wiring/service.py` | 위 컴포넌트를 이어 저작 흐름을 만든다(`AuthoringService`). 저장소 읽기·쓰기 순서와 초안별 락만 둔다 |
| `wiring/schemas.py` | `schemas/`와 계약 스키마의 레지스트리, 컴포넌트를 "입력 검증 -> 실행 -> 출력 검증"으로 감싸는 `bind` |
| `wiring/stores.py` | 백엔드를 `QuestionStore`에 주입 |
| `wiring/ids.py` | 시각·새 ID 공급(컴포넌트가 같은 입력에 같은 출력을 내도록 입력으로 넘긴다) |
| `wiring/contract.py` | 진입점의 환경 변수·purge 출력 계약 검사 |
