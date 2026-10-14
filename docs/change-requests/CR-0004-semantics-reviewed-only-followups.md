# CR-0004 reviewed_only 해석과 후속 작업 매핑

- 상태: 헤드가 승인함(boundary-contracts v1.0.0, INTERFACES.md/CONVENTIONS.md 10절 반영).

- 대상: INTERFACES.md I1, ChangeClassification.follow_ups
- 1) `reviewed_only`: 계약은 "APPROVED 검수가 있고 clinical_basis_approved가 true인 최신"이다. 나중에 반려가 덧붙은 버전을 어떻게 볼지 정해져 있지 않다. 현재 처리는 가장 최근 검수가 승인이어야 나온다(승인 철회 가능). 요청: 이 해석이 게이트의 기대와 맞는지 확인.
- 2) 후속 작업 매핑(제안): TEXT -> REGENERATE_SAMPLES; CHOICE_UNIT, SLOT, BRANCH -> REGENERATE_SAMPLES + REEVALUATE_MODEL; 수치·위험 기준·단위 범위·위험 문구가 바뀌면 CLINICAL_REVIEW 추가. 이는 기계적 표시이며 임상 판단이 아니다. 요청: 합성 데이터, model-lab이 기대하는 매핑과 맞는지 확인하고 다르면 규칙을 알려 달라.
- 3) 첫 버전의 `get_change`는 None(계약대로). 그 버전의 영향 범위를 알려야 하는지 확인.
