# STATUS (2026-10-15, 첫 구간)

## 한 일
- design.md, boundary.md, runbook.md, CHANGELOG.md, 변경 요청 5건.
- I1 QuestionReader: SQLite 구현과 메모리 구현, 같은 적합성 시험 통과.
- 저작 서비스 핵심 로직, ChangeClassification 생성, 추출기 인터페이스와 텍스트 양식 추출기, 얇은 HTTP 계층.
- Dockerfile, 스모크 시험(`scripts/smoke.sh`, `docker`로 실행해 SMOKE OK 확인).
- 후속 2: I1 HTTP 읽기 면, 읽기 토큰, survey-schema wheel, HEALTHCHECK, 라벨 통일. boundary-contracts는 v1.2.0(clients 포함).
- `python -m pytest -q`: 126개 통과. 후속 구간: list_ids, boundary-contracts v1.0.0 사용, 이미지 wheel 설치, 스모크 동적 포트.

## 남은 일
- 관리자 콘솔 UI, 화면 입력용 템플릿(필드 목록)과 코드·단위 사전(내용은 저자가 정한다).
- 소비 계약: I4 판정 조회, I8 규칙 미리보기 연동. (I7 개선 신호는 구현함. 문항 ID를 trace의 planner_output.directive에서 읽는다는 가정은 ops-feedback과 확인 필요.)
- PDF·Word 추출기(텍스트 추출 + 기존 문법 파서 재사용 가능). 실제 저자가 쓰는 형식을 확인해야 한다.
- I1 SDK를 별도 설치 가능한 패키지로 분리하고 Git 태그로 배포하는 방법(헤드와 협의).
- 저자 인증의 실제 방식(현재는 정적 토큰 표).
- 서비스 쪽 `compose.dev.yaml`은 외부 DB가 없어 만들지 않았다.

## 막힌 것
- 없음. 변경 요청 CR-0001~0005는 모두 승인됨.
