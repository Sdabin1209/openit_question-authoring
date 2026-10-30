# 영역 내부 스키마

타입 하나에 파일 하나(`<타입>.json`, JSON Schema draft 2020-12), 통과·거부 예시는 `examples/<타입>.valid*.json`, `<타입>.invalid*.json`이다. 컴포넌트와 입출력 대응은 [docs/components.md](../docs/components.md). 영역 사이 타입은 복사하지 않고 `$ref`로 계약을 가리킨다(`_common.schema.json`, `AuthoringResponses.schema.json#/$defs/Draft` 등). 검증은 `question_authoring/wiring/schemas.py`가 한다.

## 스키마 밖 조건

스키마로 쓰지 못했거나 쓰지 않은 거부 조건이다. 이유가 "상태 코드"인 것은 스키마 위반이면 HTTP 400(INVALID_RECORD)이 되는데 계약이 정한 코드가 412 등이라 와이어 형식을 바꾸지 않으려고 컴포넌트에 두었다(스키마로 옮길 수는 있다).

| # | 컴포넌트 | 조건 | 이유 |
|---|---|---|---|
| 1 | access_policy | 행위자 역할이 `allowed_roles`(+4-eyes의 검수 역할)에 속한다 | 같은 입력 안의 두 값 대조. JSON Schema는 다른 필드 값을 열거로 참조하지 못한다 |
| 2 | access_policy | `owner_id`가 `actor.id`와 같다(4-eyes) | 두 필드 값 비교 |
| 3 | draft_edit, flag_acknowledge, draft_discard | `draft.status`가 OPEN | 상태 코드(412). 스키마로 옮기면 400이 된다 |
| 4 | draft_edit | 편집 경로가 초안 문서 안에 있다(JSON 포인터 해석) | 문서 내용 의존 |
| 5 | flag_acknowledge | 확인할 `flag_ids`가 초안의 플래그에 있다 | 두 필드 사이 참조 |
| 6 | confirm_plan | 이미 확인된 초안은 같은 `preview_hash`일 때만 다시 응답, 다르면 CONFLICT | 두 필드 값 비교, 상태 코드(409) |
| 7 | confirm_plan | `status`가 OPEN, 제출한 해시 = 현재 해시, 열린 플래그 없음, 최신 버전 = 기반 버전 | 두 필드 비교, 상태 코드(412) |
| 8 | confirm_finalize | `validation_errors`가 비었다, 변경 비교가 비어 있지 않다(내용이 같으면 새 버전 없음) | 상태 코드(INVALID_RECORD 400 / 412) |
| 9 | review_followup | 임상 승인은 검수 역할만, 자기가 확인한 버전은 스스로 승인 못 함 | 기존 검수 목록과 행위자의 대조 |
| 10 | review_followup | 같은 `review_id`는 같은 내용일 때만 멱등, 다르면 CONFLICT | 기존 검수와 값 비교 |
| 11 | bank_validate | 참조 무결성(문항·슬롯·선택지 코드 중복, 없는 문항·슬롯 참조) | JSON Schema는 배열 안 키의 유일성·다른 위치 참조를 표현하지 못한다. 위반은 거부가 아니라 `errors`로 낸다 |
| 12 | extract_textform | 줄 길이·문항 수·슬롯 수·플래그 수 상한, 시간 예산 | 입력 크기·경과 시간 의존. 위반은 거부가 아니라 `status=FAILED`로 낸다(ADR 0015) |
| 13 | link_failures_read | 마감 시간(`deadline_seconds`) 경과 | 경과 시간 의존 |
| 14 | question_store | 요청한 버전이 있다, 저장된 내용의 해시가 기록·ref와 같다 | 저장된 상태 의존 |
| 15 | store_memory, store_sqlite, store_postgres | 버전 번호는 마지막 + 1, 같은 `review_id` 충돌, 원본이 정리됐으면 확정 거부, 읽기 전용 연결에는 쓰기 금지 | 저장된 상태 의존 |
| 16 | link_authoring_server | `max_cases` 1~20000, `limit` 1~500, `reviewed_only` true/false, gateway 사용자가 역할표에 있다 | 계약에 쿼리 범위 스키마가 없다(`SignalsQuery`는 숫자 형식만), 라이브러리가 쿼리 검증을 아직 지원하지 않는다(CR-0008) |

위 16행 중 스키마로 옮길 수 있는데 옮기지 않은 것은 3, 6, 7, 8행(상태 코드 이유)이다. 나머지는 스키마로 표현할 수 없다.

## 입력 순서의 변화
`Actor.id` 형식이 입력 스키마로 옮겨져, 역할도 틀리고 ID도 틀린 호출은 이전에 PERMISSION_DENIED였고 지금은 INVALID_RECORD다(둘 다 틀린 경우만).
