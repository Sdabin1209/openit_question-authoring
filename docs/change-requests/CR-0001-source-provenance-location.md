# CR-0001 문항 세트 출처(원본 해시)를 둘 곳

- 상태: 헤드가 승인함(boundary-contracts v1.0.0, INTERFACES.md/CONVENTIONS.md 10절 반영).

- 대상: boundary-contracts(ReviewRecord, ChangeClassification), 문진 스키마 QuestionBank
- 부족한 점: 브리프는 원본 자료의 해시를 "문항 세트 버전의 출처로 `ext.question-authoring`에 기록"하라고 한다. 그러나 `QuestionBank`는 `additionalProperties: false`이고 `ext`가 없으며, `ChangeClassification`은 첫 버전에 없다.
- 현재 처리: 모든 버전에 반드시 있는 `ReviewRecord.ext.question-authoring.source`에 `{source_hash, filename, size, extractor, extractor_version}`를 기록한다(화면 입력이면 `{kind: "screen"}`).
- 요청: 이 위치가 맞는지 확인해 달라. 다른 영역이 출처를 읽어야 한다면 정식 필드로 올릴지 정해 달라(CONVENTIONS 1절).
