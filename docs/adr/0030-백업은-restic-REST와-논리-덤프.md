# ADR 0030: 백업은 논리 덤프를 restic 으로 platform 백업 저장소(REST)에 보낸다

## 상태
채택(2026-11-04, 계약 3.4.7 RUNTIME R10.1·R9.2, CommonEnv `OPENIT_BACKUP_*`, BackupJobResult).

## 맥락
영역은 자기 데이터를 꺼내 자기 영역 게이트웨이 출구(R1.8)로 platform 백업 저장소(HTTP, restic REST 등)에 보낸다. 주기·보존·복구 시험은 영역이 잡 선언과 ADR 로 정한다. 마지막 줄 JSON 은 BackupJobResult, 종료 코드 0·75·1.

## 결정
- 잡 `python -m question_authoring backup`(deploy-jobs.json, `timeout_seconds` 1800, 멱등). 표 다섯(versions·reviews·changes·drafts·blobs)을 psycopg `COPY ... TO STDOUT` 으로 REPEATABLE READ 읽기 전용 트랜잭션 하나에서 꺼내(한 시점의 일관된 모습) `restic backup --stdin` 으로 보낸다. 스키마는 마이그레이션이 만들므로 데이터만 낸다.
- 출구: restic(Go)에 `HTTPS_PROXY=OPENIT_EGRESS_PROXY`(CONNECT 터널)만 준다. 저장소 주소는 `OPENIT_BACKUP_URL`(https만, `require_tls_url`), 자격은 `OPENIT_BACKUP_CREDENTIALS_FILE`(`사용자:암호` 한 줄, RFC 7617)을 restic 표준 환경 변수 `RESTIC_REST_USERNAME`·`RESTIC_REST_PASSWORD` 로, 저장소 암호는 `RESTIC_PASSWORD_FILE`. 하위 프로세스에는 필요한 이름만 명시해 넘긴다(R9.1). argv·로그·출력에 비밀이 없다(R6.3).
- 종료: 성공 0, 저장소에 닿지 못함(UNAVAILABLE) 75(EX_TEMPFAIL), 그 밖 1. 어느 경우든 BackupJobResult 한 줄(`failed` 면 `error` 에 BoundaryError 모양). 저장소가 응답하지 않아도 서비스 출력은 바뀌지 않는다(잡은 서비스 밖 프로세스).
- 주기: 선언하지 않는다(주기는 deploy 의 배포 설정, R10.12). 보존: restic 저장소 쪽 정책(`forget`)은 platform 백업 저장소 소관이다.
- 복구: 새 DB 에 마이그레이션을 적용한 뒤 `restic dump <snapshot> question-authoring.copy` 의 COPY 블록을 `psql` 로 되돌린다(docs/runbook.md). 복구 시험은 실제 백업 저장소가 있어야 하며 이 세션에서는 하지 않았다(미확인).

## 왜
- 표준: restic REST 서버 프로토콜(restic 문서), RFC 7617, 12-Factor XI(출력은 표준 출력), EX_TEMPFAIL=75(sysexits.h).
- 논리 덤프를 psycopg 로 하는 이유: `pg_dump` 는 서버보다 낮은 클라이언트를 거부해 이미지가 서버 버전에 묶인다. 표가 다섯뿐이라 COPY 로 충분하다. 더 큰 표를 가진 영역이면 다시 본다.
- restic 을 Debian 패키지로 설치하는 이유: 표준 저장소의 서명된 패키지, 별도 다운로드 없음. 버전 고정은 이미지 다이제스트로 한다(CI 가 게시, D24).
- 1800초: 덤프 문장 상한과 같은 시작값, 측정하지 않았다.

## 검토한 대안과 버리는 이유
- `pg_dump`: 위 이유.
- 직접 restic REST 프로토콜 구현: 암호화·중복 제거를 새로 만드는 일이라 버린다.
- DB 물리 백업(PITR): 영역 하나의 작은 DB 에는 과하다.

## 결과
얻는 것: 영역 밖으로 나가는 유일한 데이터 경로가 하나이고 게이트웨이 허용 목록(`external:backup`)으로 통제된다. 감수하는 것: 이미지에 restic 이 들어간다. 확인하지 못한 것: 실제 restic REST 서버와의 왕복, 복구 시험.

## 되돌리는 조건
platform 백업 저장소가 restic 이 아닌 프로토콜을 정하면 새 ADR 로 대체한다.
