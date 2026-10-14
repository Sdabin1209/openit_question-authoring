# CR-0002 QuestionBank.version(정수)의 의미

- 상태: 헤드가 승인함(boundary-contracts v1.0.0, INTERFACES.md/CONVENTIONS.md 10절 반영).

- 대상: 문진 스키마 QuestionBank, INTERFACES.md I1
- 부족한 점: `QuestionBank.version`은 정수(최소 0), `ArtifactRef.version`은 문자열이다. 둘의 관계가 정해져 있지 않다.
- 현재 처리: 문항 세트 버전 번호 n(1부터)을 `ArtifactRef.version="n"`과 `QuestionBank.version=n` 모두에 쓴다. 따라서 내용이 같아도 번호가 다르면 해시가 다르다.
- 요청: 문진 서비스가 `QuestionBank.version`을 다른 뜻(예: 스키마 판)으로 쓰는지 확인해 달라. 다르면 알려 주면 바꾼다.
