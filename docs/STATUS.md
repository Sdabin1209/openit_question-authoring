# STATUS (2026-10-27, 0.6.0 기준)

## 한 일
- design.md, boundary.md, runbook.md, CHANGELOG.md, 변경 요청 5건.
- I1 QuestionReader: PostgreSQL(운영)·SQLite(개발·시험)·메모리 구현, 같은 적합성 시험을 모두에서 통과(PostgreSQL은 `TEST_DATABASE_URL` 필요, 없으면 SKIP).
- 저작 서비스 핵심 로직, ChangeClassification 생성, 추출기 인터페이스와 텍스트 양식 추출기, 얇은 HTTP 계층.
- Dockerfile, 스모크 시험(`scripts/smoke.sh`, `docker`로 실행해 SMOKE OK 확인).
- 후속 2: I1 HTTP 읽기 면, 읽기 토큰, survey-schema wheel, HEALTHCHECK, 라벨 통일. boundary-contracts는 v2.0.2(clients 포함).
- `python -m pytest -q`: PostgreSQL 포함 전체 통과(0.6.0, `TEST_DATABASE_URL` 설정 시). 미설정 시 PostgreSQL 항목은 SKIP.
- turn-engine·model-runtime: 이 영역은 둘을 설치하지 않고 코드에서도 쓰지 않는다. 이미지 라벨 `io.openit.turn-engine`, `io.openit.model-runtime`은 `none`이다(Dockerfile). turn-engine은 I8 규칙 미리보기를 붙일 때 처음 필요해진다(미구현).

## 남은 일
- 관리자 콘솔 UI, 화면 입력용 템플릿(필드 목록)과 코드·단위 사전(내용은 저자가 정한다).
- 소비 계약: I4 판정 조회, I8 규칙 미리보기 연동. (I7 개선 신호는 구현함. 문항 ID를 trace의 planner_output.directive에서 읽는다는 가정은 ops-feedback과 확인 필요.)
- PDF·Word 추출기(텍스트 추출 + 기존 문법 파서 재사용 가능). 실제 저자가 쓰는 형식을 확인해야 한다.
- I1 SDK를 별도 설치 가능한 패키지로 분리하고 Git 태그로 배포하는 방법(헤드와 협의).
- 저자 인증의 실제 방식(현재는 정적 토큰 표).
- 운영 PostgreSQL의 데이터베이스·계정 발급과 `QA_DATABASE_URL_FILE` 비밀 배치는 deploy 몫(이 리포는 변수 계약만 정함). 읽기 전용 계정 분리는 미정.
- 기존 SQLite 파일 데이터를 PostgreSQL로 옮기는 도구는 없다(운영 데이터가 아직 없다고 가정, 확인 필요).

## 막힌 것
- 없음. 변경 요청 CR-0001~0005는 모두 승인됨.
