# ADR 0001: 저장소 SQLite 개발 PostgreSQL 운영

- 상태: 채택(운영 PostgreSQL은 0.6.0, 2026-10-27)
- 날짜: 2026-10-28

## 맥락
문항 세트 버전·검수·변경 분류(불변)와 초안·원본 자료(가변)를 저장할 곳이 필요하다. 이웃은 `QuestionReader`(I1)로만 읽는다. 사용자는 관리자뿐이라 동시 쓰기는 적다. 처음(0.1~0.5)에는 SQLite 단일 파일만 있었고, 운영 요구가 생기면서 PostgreSQL을 추가했다.

## 결정
`AuthoringStore` 인터페이스 뒤에 구현 셋: 메모리(이웃이 시험에 쓰는 가짜 구현), SQLite(개발·시험 기본, `QA_DB_PATH`), PostgreSQL(운영, `QA_DATABASE_URL`이 있으면). 세 구현은 같은 적합성 시험을 통과해야 한다.

## 왜 (따른 표준과 근거)
- SQLite를 개발·시험 기본으로 쓰는 이유: 서버·계정·네트워크 없이 단일 파일(또는 `:memory:`)로 돌아 컨테이너 밖 개발(워크스페이스 CLAUDE.md '컨테이너와 개발 D22')과 이웃 영역의 단독 시험이 쉽다. 표준 라이브러리(`sqlite3`)라 설치할 것이 없다. 출처: 워크스페이스 CLAUDE.md D22, design.md 5절('한 서버, 한 프로세스, 관리자만 쓰는 사용자'라는 전제, 제안).
- 운영을 PostgreSQL로 한 이유: 영역별 파일 DB보다 서비스별 데이터베이스·계정, 백업(`pg_dump`), 접근 제어, 동시 쓰기를 표준 도구로 다룰 수 있다(CHANGELOG 0.6.0 기록). 다른 영역도 같은 서버 DB를 쓰는 deploy 구성(`deploy/compose.yaml` postgres)과 맞춘다. 불변성은 트리거(UPDATE/DELETE 거부)로 DB가 직접 막는다(D25).
- 쓰기 직렬화: 트랜잭션 수준 advisory lock(SQLite의 `BEGIN IMMEDIATE`에 해당)으로 '다음 버전은 마지막+1' 검사와 삽입 사이에 다른 쓰기가 끼지 못하게 한다. PostgreSQL 문서의 표준 기법이다.
- 드라이버 `psycopg[binary]` 3.3.6(고정): 현재 PostgreSQL 표준 Python 드라이버(psycopg 3). 다른 드라이버와의 성능 비교는 하지 않았다(측정 안 함).

## 검토한 대안과 버리는 이유
- 운영에서도 SQLite 파일: 동시 쓰기·백업·접근 제어가 약하고 컨테이너 tmpfs에서는 재시작마다 사라진다(전수 점검 P6가 이 위험을 지적). 버림.
- ORM(SQLAlchemy 등): 테이블 6개와 단순 SQL이라 의존 추가의 이득이 작다고 판단. 비교 측정은 없다(근거 기록 없음, 판단).
- 문서 DB·객체 저장소: 버전·검수 사이 관계와 트랜잭션 원자성이 필요해 관계형이 맞다.

## 결과
얻는 것: 같은 시험으로 세 구현의 동치를 보장, 운영은 표준 백업·계정. 감수하는 것: SQL을 두 방언으로 유지한다. 운영 DB가 없으면 PostgreSQL 시험은 SKIP(`TEST_DATABASE_URL`). 운영 구성에서 DB URL을 빠뜨리면 시작을 거부한다(`QA_DEV_MODE` 없을 때).

## 되돌리는 조건
쓰기 경합이나 규모가 PostgreSQL 한 대로 부족해지면 `AuthoringStore`만 바꾼다. SQLite를 개발 기본에서 내리는 조건은 개발 환경에서 PostgreSQL 컨테이너가 항상 쉽게 뜰 때(현재는 `deploy/scripts/dev-postgres.sh`가 있으나 필수는 아님).
