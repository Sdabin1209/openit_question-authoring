# CR-0006 임상 근거 승인은 저자와 다른 검수자(4-eyes) 의미 반영

- 상태: 제안(헤드 확인 대기). 서버 동작은 하위 호환 범위에서 이미 구현했다(D88 U9, `QA_REQUIRE_TWO_PERSON` 기본 true, ADR 0006).
- 대상: INTERFACES.md I1(`reviewed_only`의 의미), `ReviewRecord` 사용 규약, D30 문구.
- 무엇이 바뀌나: 저자의 확인(`confirm`)이 만드는 첫 `ReviewRecord`는 `decision=APPROVED`, `clinical_basis_approved=false`다. 임상 근거 승인(true)은 `reviewer_role=clinical-reviewer`인 주체가 저자와 다른 ID로 `add_review`한 뒤에만 생긴다. 필드·열거형은 그대로다.
- 이웃에 미치는 영향: `latest(reviewed_only=True)`에 새 버전이 나타나는 시점이 저자 확인 직후가 아니라 검수자 승인 뒤다. 이를 가정한 소비자(게이트, 합성 데이터, 시험)는 검수 완료 버전이 한 단계 늦게 생긴다는 점을 알아야 한다. 이 영역은 이웃 코드를 읽지 않아 영향 범위를 확인하지 못했다.
- 제안: CONVENTIONS/INTERFACES의 `ReviewRecord` 설명에 '임상 근거 승인은 저자와 다른 주체가 한다(권장 기본)'를 적고, D30의 '저자의 확인이 임상 승인'을 D88로 대체됨으로 표시. `reviewer_role` 값 `clinical-reviewer`를 예시로 둔다.
- 하지 않은 것: 계약 파일 수정(헤드 소유), 검수자의 임상 자격 확인(이 서비스는 역할표만 본다).
