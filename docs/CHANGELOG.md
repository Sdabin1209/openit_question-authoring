# 변경 이력

## 0.1.0 (2026-10-15) 첫 구간

- 문항 저장소: `AuthoringStore` 인터페이스, SQLite 구현(불변 트리거, 읽기 전용 연결), 메모리 구현. I1 `QuestionReader`를 두 구현이 같은 적합성 시험으로 통과.
- 저작 서비스: 초안, 편집, 플래그 확인, 미리보기, 저자 확인(ReviewRecord), 이후 검수, 환자 정보 경고.
- ChangeClassification 생성(문구 / 선택지·단위 / 슬롯 / 분기, 영향 슬롯, 후속 작업).
- 문항 자료 등록: 추출기 인터페이스와 레지스트리, 텍스트 양식 추출기, FALLBACK, 원본 해시 출처 기록.
- 얇은 HTTP 계층, Dockerfile, 스모크 시험.
- 문서: design, boundary, runbook, STATUS, 변경 요청 5건.
