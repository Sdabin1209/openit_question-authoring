# 표준성·근거 점검 (question-authoring, 2026-10-29)

기준서: `head/docs/audit/standards-audit-spec.md`. 읽기 전용 점검이며 코드·설정·시험은 고치지 않았다. 분류: S, S-, N, N-, O, ?.
읽은 것: CLAUDE.md, README.md, docs/adr/ 0001~0012 전부, docs/design.md(1~3절과 발췌만), docs/boundary.md(발췌), `question_authoring/` 중 `__main__.py`, `http_api.py`, `store/sqlite.py`(1~120줄), `store/postgres.py`(1~60, 335~370줄과 grep), `service.py`(발췌), `signals.py`, `extractors/textform.py`(발췌), `Dockerfile`(grep). 줄 번호는 이 저장소 현재 커밋 기준이다.
"표준" 칸은 이름을 아는 것만 적었다. 확인 못 하면 "확인 못 함"이라 썼다.

## 결과표

| # | 요소 | 분류 | 표준(이름) | 근거 위치 | 증거 |
|---|---|---|---|---|---|
| 1 | 문항 세트 버전 불변(수정=새 버전, 번호 +1) | S | 내용 주소 지정/불변 레코드(git, 컨테이너 digest와 같은 원리, ADR이 든 관행) | ADR 0002, D25 | design.md 3절, store/base.py |
| 2 | 불변을 DB 트리거로 강제(versions, reviews, changes의 UPDATE/DELETE 거부) | S- | PostgreSQL/SQLite 트리거(DB 기능). 삽입 전용 테이블 관행 | ADR 0001·0002에 언급, 트리거 방식 자체를 고른 이유는 따로 없음 | sqlite.py:27~37, postgres.py:157 |
| 3 | content_hash = `canonical.sha256_hash(QuestionBank)`, 읽을 때 재계산 후 불일치 시 INTEGRITY_ERROR | S | SHA-256(FIPS 180-4). 직렬화 규칙은 계약 패키지 `canonical` 소관 | ADR 0002 | store/base.py:19, preview.py:20 |
| 4 | canonical 직렬화 형식(키 정렬, 인코딩 등) | ? | RFC 8785(JCS)와 같은지 확인 못 함. `boundary_contracts/canonical.py`는 읽기 허용 범위 밖이라 읽지 않았다 | ADR 0002("계약의 canonical이 정한 것") | sqlite.py:46은 별도로 `json.dumps(sort_keys=True)`(저장용, 해시 아님) |
| 5 | 버전 번호 = 1부터 1씩 느는 정수, `QuestionBank.version`과 같게 | N | 표준 아님(계약 규약) | CR-0002(헤드 승인), design.md 3절 | design.md 3절 |
| 6 | 4-eyes: 저자 확인은 `clinical_basis_approved=false`, 임상 승인은 `clinical-reviewer`가 다른 주체일 때만 | S | 업무 분리(separation of duties, 4-eyes). NIST SP 800-53 AC-5가 같은 개념(ADR은 개념 이름만 적음) | ADR 0006, D88 U9 | service.py:146, 428, 463~470 |
| 7 | 4-eyes 동일 주체 판정 = 주체 ID(토큰 역할이 아님), `AUTHOR_CONFIRMATION`의 reviewer_id와 비교 | S- | 업무 분리 관행의 구현 선택 | ADR 0006(대안 "역할만 구분" 버림) | service.py:469 |
| 8 | 4-eyes를 `QA_REQUIRE_TWO_PERSON=false`로 끄는 스위치, 끄면 /readyz 경고만 | N | 표준 아님 | ADR 0006(검수자 없으면 멈춤) | __main__.py:129~131, http_api.py:75 |
| 9 | 저자 확인 직후 reviewed_only 판정을 "가장 최근 검수가 APPROVED+true"로 보수적으로 해석 | N | 표준 아님(계약 문구보다 엄격) | CR-0004, design.md 3.1 | design.md 3.1 |
| 10 | 인증 전달 형식 `Authorization: Bearer <토큰>` | S | RFC 6750(Bearer 토큰 사용) | ADR 0010(형식 이유는 안 적음) | http_api.py:91~94 |
| 11 | 정적 토큰 표 `토큰:ID:역할`을 비밀 파일로 받아 서비스가 직접 보관·대조(발급·회전·폐기는 사람이 파일을 바꾸고 재시작) | N | 표준 아님. 대안 JWT는 "비교 안 함"(ADR 0010). OAuth 2.0/OIDC 같은 발급 체계는 검토 기록 없음 | ADR 0010 | __main__.py:107~112, http_api.py:154~155 |
| 12 | 토큰 비교가 dict 조회(상수 시간 비교 아님) | N | 상수 시간 비교(`hmac.compare_digest`)가 일반 권고(OWASP). 이 영역은 따르지 않음 | ADR 0010("외부 비노출이라 안 고침", L 등급) | http_api.py:154, 156 |
| 13 | 토큰 포맷 `토큰:ID:역할`을 쉼표/콜론으로 쪼갬(토큰에 `:`·`,` 불가, 형식 오류는 ValueError) | N- | 표준 아님 | 근거 기록 없음 | __main__.py:108~109 |
| 14 | gateway 경유 사용자 식별 `X-Openit-User`는 gateway 토큰 요청에서만 신뢰 | N | 표준 아님(계약 HTTP.md 규약). `계약 HTTP.md` 본문은 읽지 않았고 ADR 인용에만 근거 | ADR 0010 | http_api.py:156~162 |
| 15 | 역할표 `QA_USER_ROLES`(사용자 ID→역할)를 이 서비스 설정에 직접 둠. 계정 목록·역할 부여를 서비스가 관리 | O | IdP/gateway가 역할(그룹)을 주는 방식이 표준. 재발명 여부 판단: 사용자·역할 관리를 직접 운영 | ADR 0010에 이유("gateway에 역할 헤더 규약 없음, 계약 변경이라 안 만듦")는 있음. 가져다 쓰지 않은 이유로는 부분 근거 | __main__.py:114~119, http_api.py:158 |
| 16 | 사용자 ID 문법 `_USER_RE`(local 60/domain 60), 이메일 `@`→`:at:` 치환 | N | 표준 아님. 총 128자는 계약 `Id` 정합 | http_api.py:41, 161 주석, ADR 0010 | http_api.py:42, 162 |
| 17 | 일반 ID 문법 `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$` | N | 표준 아님(계약 `Id` 규약을 따른 것으로 보임. 계약 정의는 읽지 않아 확인 못 함) | 근거 기록 없음(ADR·주석에 정규식 이유 없음) | service.py:30, 163~165 |
| 18 | 생성 ID `접두사-uuid4().hex[:16]`(64비트로 자름) | N- | UUID v4(RFC 4122/9562)를 자름. 자른 이유·충돌 확률 계산 없음 | 근거 기록 없음 | service.py:58~59 |
| 19 | 저장소: 개발·시험 SQLite, 운영 PostgreSQL, 메모리 가짜 구현, 같은 적합성 시험 | S | PostgreSQL/SQLite 문서화된 사용. 12-factor(설정은 환경) 해당 부분 | ADR 0001 | postgres.py, sqlite.py, __main__.py:57~65 |
| 20 | 쓰기 직렬화: PG 트랜잭션 advisory lock, SQLite `BEGIN IMMEDIATE` | S | PostgreSQL 명시적 락킹 문서(advisory lock), SQLite 트랜잭션 문서 | ADR 0001 | postgres.py:56, 153, 237, sqlite.py:112 |
| 21 | 드라이버 psycopg 3.3.6 고정, ORM 없음 | S | PostgreSQL Python 표준 드라이버. 비교 측정 없음(ADR이 명시) | ADR 0001 | postgres.py:20~21 |
| 22 | 데이터 모델: 문서 본문(`bank`, `record`)을 JSON 문자열 TEXT 열로 저장(JSONB 아님), 시각도 TEXT | N- | PostgreSQL은 JSONB/TIMESTAMPTZ를 제공. TEXT를 고른 이유 없음 | ADR 0001에 테이블 6개·단순 SQL 언급뿐, 열 타입 이유 기록 없음 | postgres.py:28~40, sqlite.py:13~25 |
| 23 | 스키마 관리: `CREATE IF NOT EXISTS` + meta `schema_version="1"` 불일치면 거부, 열 추가는 코드 안 `ALTER TABLE`. 마이그레이션 도구/이력 없음 | N- | 표준 관행은 버전 붙은 마이그레이션 도구(Alembic 등)이나 이 영역은 사용 안 함 | 근거 기록 없음 | sqlite.py:28, 70~75 |
| 24 | 시각 형식 `%Y-%m-%dT%H:%M:%SZ`(UTC) | S- | RFC 3339/ISO 8601 UTC | 근거 기록 없음(계약 형식으로 추정, 확인 못 함) | service.py:54~55, sqlite.py:40~42 |
| 25 | 원본 자료를 `blobs`(sha256 키, BLOB/BYTEA)에 DB 보관 | N | 표준 아님. 대안(파일 볼륨, 객체 저장소) 비교 기록 | ADR 0003 | sqlite.py:25, postgres.py:40 |
| 26 | 정리 SQL이 JSON 문자열 전체에서 해시를 부분 문자열로 찾음(`position(해시 in source/record)`) | N- | 외래키/참조 테이블 대신 텍스트 검색. 이유·성능 기록 없음 | 근거 기록 없음 | postgres.py:365~368 |
| 27 | 자료 파싱: 직접 쓴 규칙 기반 선형 줄 파서, 추출기 인터페이스 | N | 표준 아님. LLM·정규식·re2·파서 라이브러리 비교(일부는 "비교 안 함") | ADR 0004, design.md 7.1 | extractors/textform.py:55~100 |
| 28 | 입력 파일 형식 "텍스트 양식"(`문항 <ID>: <이름>`, `슬롯 ...: 필수|선택` 줄 문법)을 자체 정의 | N- | 기존 형식(YAML, CSV, Markdown 표 등)과 비교한 기록을 읽지 못함. design.md 7절은 발췌만 읽어 확정 못 함 | ADR 0004는 파서 구현 비교만, 형식 선택 비교 없음 | textform.py:16~17 |
| 29 | ReDoS 대응: 줄 길이 상한, `str.find` 분할, 되돌림 없는 알고리즘, 협조적 시간 예산 | S | OWASP ReDoS 대응 관행(입력 상한, 선형 알고리즘). ADR은 이름을 "알려진 문제"로만 적음 | ADR 0004(실측 포함) | textform.py:71~73, 78~79 |
| 30 | 파서 상한 값: 줄 4096자, 문항 2000, 슬롯 200, 플래그 5000, 시간 20초 | N- | 측정·표준·제약 어느 것도 아님(여유값) | ADR 0004가 "근거 기록 없음(판단), 측정 안 함"이라 명시 | textform.py:20~23, 48 |
| 31 | 환자정보 탐지 정규식(주민번호, 전화, 이메일), NFKC 정규화, 경고 전용 | N | 표준 아님(비식별 표준 도구는 아님, ADR이 한계 명시). NER 대안 비교 | ADR 0005 | service.py:33~41 |
| 32 | HTTP 서버 = 표준 라이브러리 `ThreadingHTTPServer`, HTTP/1.0 기본, 청크 미지원 | N- | 표준 아님. Python 문서가 `http.server`를 운영용으로 권하지 않는다고 밝힘(내 기억, 이번에 문서 재확인 안 함). 프레임워크/WSGI 서버와 비교한 기록이 없다 | ADR 0012가 "근거 기록 없음(비교 측정·검토 기록이 없다)"이라 명시 | http_api.py:11, 315 |
| 33 | 서버가 제공하지 않아 직접 구현: 동시 요청 세마포어, 소켓 timeout, 인증 전 본문 미수신, Content-Length 검증, 우아한 종료, 예외→JSON | O | 성숙한 서버/프레임워크(gunicorn, uvicorn 등)가 제공하는 기능을 직접 유지보수 | ADR 0012가 대가를 인정하고 되돌리는 조건을 적음. 가져다 쓰지 않은 이유는 "의존이 늘어서"뿐 | http_api.py:235~307, __main__.py:163~178 |
| 34 | 상태 코드: 본문 초과를 413 대신 400, 내부 오류를 500 대신 503 | N | RFC 9110은 413·500 정의. 계약 CONVENTIONS 9절 표가 고정(그 표는 읽지 않음) | http_api.py:87, 295 주석 | http_api.py:19~20, 87~89, 295 |
| 35 | 본문 상한 8MiB | N- | 측정·표준·제약 아님 | ADR 0007·http_api.py:21이 "도입 시 근거 기록 없음"이라 명시 | http_api.py:22 |
| 36 | 동시 요청 32, 요청 소켓 30초, 큰 자료 쓰기 문장 30초 | N- | 측정 안 함(여유값) | ADR 0007이 "근거 기록 없음(여유값)" 명시 | __main__.py:64, 160~161, http_api.py:239 |
| 37 | DB 접속 3초, 문장 3초(+1초 클라이언트 소켓 마감), /readyz 2초 | N | FAULT_POLICY(내부 문서, 읽지 않음)에 의존. libpq connect_timeout 정수 초 동작은 문서 인용 | ADR 0007(정상 시 수 ms, 이 PC 한 환경 실측, "제안") | __main__.py:61~63, postgres.py:131, 206 |
| 38 | timeout 입력 검증 범위 `0 < v <= 600` | N- | 600 상한의 이유 없음 | 근거 기록 없음 | __main__.py:52 |
| 39 | 종료 유예 8초(docker stop 기본 10초보다 짧게) | S | Docker `stop` 기본 10초 후 SIGKILL | 코드 주석(Docker 문서, 측정 안 함) | __main__.py:170 |
| 40 | SIGTERM/SIGINT 처리 후 서버 종료, root 아닌 사용자(10001), HEALTHCHECK | S | 워크스페이스 D22(컨테이너 규칙). 12-factor disposability | 워크스페이스 CLAUDE.md | __main__.py:163~167, Dockerfile:20, 26 |
| 41 | 비밀을 `NAME_FILE`로 받고 `read_secret`로 읽음. 이미지에 비밀 없음 | S | 파일 기반 비밀(Docker/K8s secrets 관행) | D84 | __main__.py:58, 108, 113, 126, 135 |
| 42 | 로그를 표준 출력으로 냄. 접근 로그에 클라이언트 주소 포함 | S- | 12-factor(로그는 이벤트 스트림) | 근거 주석: "개인 정보 없는 요청 줄만"(http_api.py:312) | http_api.py:312~313 |
| 43 | 확인 흐름: 미리보기 해시 대조 + `confirming`에 번호·시각을 예약해 재시도 멱등 | N | 서버 쪽 멱등 키 관행(ADR이 "표준 기법"이라 부르나 이름 없음). 클라이언트 멱등 키 대안 비교 | ADR 0008 | service.py(_confirm 380~450), preview.py:19~26 |
| 44 | 초안 동시 편집을 프로세스 안 `RLock`(draft_id별) 직렬화, 단일 프로세스 전제 | N | 표준 아님(저장소 revision 비교 저장이 일반적). 여러 프로세스 시 깨짐을 ADR이 인정 | ADR 0008 | service.py:132~133, 149~151 |
| 45 | 개선 신호: 상류(I7) 목록을 읽고 읽을 때마다 문항별 건수·유형 분포·confusion 계산, 저장하지 않음 | N | 표준 아님. ops-feedback이 계산하는 대안은 "경계 합의 기록 없음" | ADR 0009 | signals.py:57~124 |
| 46 | 신호 상수: `CONFUSION_MIN=2`, `max_cases` 기본 2000/상한 20000, 호출 5초/총 20초 | N- | 측정·표준 아님 | CONFUSION_MIN·max_cases는 근거 기록 없음. 5초/20초는 ADR 0007이 "근거 기록 없음(여유값)" | signals.py:19~20, http_api.py:188, __main__.py:136, 138 |
| 47 | trace 읽기: `extract_input.question_id`만 사용, 읽기 실패는 UNAVAILABLE/PERMISSION_DENIED로 실패 처리, 그 밖은 건너뜀 집계 | N | 표준 아님(TurnTrace 계약 필드, 이번에 스키마 파일은 읽지 않음) | ADR 0009, signals.py 머리 주석 | signals.py:33~42, 82~90 |
| 48 | trace 필드가 실제 ops-feedback 출력과 맞는지 | ? | 확인 못 함(README가 "확인 못 한 것"으로 명시, 이웃 영역은 읽을 수 없다) | README 현재 상태, ADR 0009 결과 | signals.py:33~42 |
| 49 | 보존 정리 `purge`: run-once, 멱등, 확정된 버전·검수는 보존, 확인과 삭제를 한 문장으로 | N | 표준 아님. 스케줄은 deploy(D39) | ADR 0011 | __main__.py:142~147, 152~157, postgres.py:362~369 |
| 50 | 보존 기간 30일(초안·고아 원본), 무기한·0 거부 | N- | 법령·병원 정책·측정 어디서도 나오지 않음 | ADR 0011이 "근거 기록 없음, 병원 확정 필요" 명시 (D88이 "잠정"으로 정함) | __main__.py:83~89, 146~147 |
| 51 | 운영 구성 거부(`QA_DEV_MODE` 없으면 DB URL·토큰 필수) | S- | 12-factor(설정 분리), fail fast | ADR 0001·0010에 언급 | __main__.py:92~100 |
| 52 | 목록 페이지 limit 기본 100, 상한 500(HTTP)/1000(store) | N- | 표준 아님. 두 상한이 다른 이유도 없음 | 근거 기록 없음 | http_api.py:103~107, store/base.py:15 |

## N-, O, ? 목록

- N- #13 토큰 포맷 `토큰:ID:역할`: 형식 선택 이유 없음(`__main__.py:108`).
- N- #18 생성 ID uuid4를 16자로 자름: 이유·충돌 계산 없음(`service.py:59`).
- N- #22 JSON을 TEXT로 저장(JSONB/TIMESTAMPTZ 아님): 열 타입 선택 이유 없음(`postgres.py:28~40`).
- N- #23 스키마 마이그레이션 도구·이력 없음(`sqlite.py:28,70`).
- N- #26 정리 SQL이 JSON 문자열 부분 검색: 참조 구조 선택 이유 없음(`postgres.py:365`).
- N- #28 텍스트 양식 줄 문법 자체: 기존 형식과의 비교 없음(design.md 7절은 일부만 읽음)(`textform.py:16`).
- N- #30 파서 상한 값 4096/2000/200/5000/20초: 여유값, 측정 안 함(ADR 0004).
- N- #32 표준 라이브러리 HTTP 서버: 비교·검토 기록 없음(ADR 0012).
- N- #35 본문 8MiB: 근거 기록 없음(`http_api.py:22`).
- N- #36 동시 32, 소켓 30초, 큰 쓰기 30초: 측정 안 함(ADR 0007).
- N- #38 timeout 입력 상한 600초: 이유 없음(`__main__.py:52`).
- N- #46 신호 상수 CONFUSION_MIN 2, max_cases 2000/20000, 5초/20초: 근거 기록 없음.
- N- #50 보존 30일: 병원 정책 대기, 근거 기록 없음(ADR 0011).
- N- #52 페이지 limit 100/500/1000: 근거 기록 없음.
- O #15 사용자·역할표를 서비스 설정에 직접 둠(IdP/gateway가 줄 역할을 직접 관리). ADR 0010에 부분 이유(gateway에 역할 헤더 규약 없음)만 있다.
- O #33 동시성 상한·소켓 timeout·본문 검증·우아한 종료를 직접 구현(성숙한 서버를 쓰면 대부분 제공). ADR 0012는 대가만 인정한다.
- ? #4 canonical 직렬화가 RFC 8785(JCS)인지: 계약 패키지 소스를 읽지 못해 확인 못 함.
- ? #48 trace 필드(`extract_input.question_id`)가 ops-feedback 실제 출력과 일치하는지: 이웃 영역을 읽을 수 없어 확인 못 함(README도 미확인으로 적음).

## 분류별 개수

S 11, S- 5, N 18, N- 14, O 2, ? 2 (합 52).

## 점검하지 못한 것

- `boundary_contracts`(canonical, clients, 계약 HTTP.md, CONVENTIONS, FAULT_POLICY)와 `openit-survey-schema` 본문: 읽기 허용 범위 밖이라 읽지 않았다. 계약에서 온 선택(ID 문법, 상태 코드 표, 해시 직렬화, `X-Openit-User`)은 ADR·주석 인용에만 의존한다.
- design.md 4절 이후(도메인 모델, 확인 절차, 변경 분류 표, 7절 텍스트 양식 문법, 8절 API 표)와 boundary.md 대부분, runbook.md, CHANGELOG.md, STATUS.md, brief.md: 발췌·grep만 했거나 읽지 않았다.
- `store/postgres.py` 중 60~335줄 대부분, `store/memory.py`, `classify.py`, `validation.py`, `preview.py` 대부분, `service.py` 확인·편집 본문: 훑지 않았다. 변경 분류 규칙과 편집 경로 문법(JSON Pointer 유사 `~0 ~1`)의 표준성은 점검하지 못했다.
- tests/, scripts/, requirements.lock(의존 선택), Dockerfile 전문, CI·이미지 라벨.
- 실행 검증은 하지 않았다(시험, 서버 기동, 스모크). 시험 개수는 README를 인용하지 않았다.
- Python `http.server` 문서의 "운영용 아님" 문구는 기억이며 이번에 문서로 재확인하지 못했다(#32).
