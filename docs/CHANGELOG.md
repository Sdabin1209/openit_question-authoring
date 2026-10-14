# 변경 이력

## 0.2.0 (2026-10-15) 헤드 결정 반영

- boundary-contracts v1.0.0 사용: 자체 스키마 위치 코드를 지우고 `boundary_contracts.validator`를 쓴다. `QA_BOUNDARY_SCHEMAS` 삭제.
- I1에 `list_ids(cursor, limit)` 추가(SQLite, 메모리, 적합성 시험).
- 이미지: `git archive v1.0.0`으로 만든 wheel 설치(`scripts/build_contract_wheel.sh`), vendor 복사 삭제.
- 스모크: 호스트 포트를 Docker가 배정, 컨테이너 이름에 리포지토리 이름.
- CR-0001~0005 승인.

## 0.1.0 (2026-10-15) 첫 구간

- 문항 저장소: `AuthoringStore` 인터페이스, SQLite 구현(불변 트리거, 읽기 전용 연결), 메모리 구현. I1 `QuestionReader`를 두 구현이 같은 적합성 시험으로 통과.
- 저작 서비스: 초안, 편집, 플래그 확인, 미리보기, 저자 확인(ReviewRecord), 이후 검수, 환자 정보 경고.
- ChangeClassification 생성(문구 / 선택지·단위 / 슬롯 / 분기, 영향 슬롯, 후속 작업).
- 문항 자료 등록: 추출기 인터페이스와 레지스트리, 텍스트 양식 추출기, FALLBACK, 원본 해시 출처 기록.
- 얇은 HTTP 계층, Dockerfile, 스모크 시험.
- 문서: design, boundary, runbook, STATUS, 변경 요청 5건.
