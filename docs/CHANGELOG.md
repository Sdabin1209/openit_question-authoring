# 변경 이력

## 0.5.0 (2026-10-27) 비밀은 파일로 (D84)

- boundary-contracts v2.0.2로 올림(`pipeline_schema_version` 2.0). 토큰 3종을 `boundary_contracts.read_secret`으로 읽는다(`NAME_FILE` 우선, 없으면 `NAME`). 스모크는 비밀을 파일로 주입하고 `inspect`/`exec env`에 평문이 없음을 확인한다. `tests/test_secrets.py` 추가(로그·/readyz·오류 본문·예외 메시지에 값 없음). 개선 신호 시험 데이터를 v2.0으로 갱신.

## 0.4.0 (2026-10-15) 개선 신호 수신함 (D52)

- `GET /v1/signals`: I7 실패 사례로 문항 세트·문항별 건수, 유형 분포, 혼동, CONFIRMED 수를 낸다(읽기 전용, 저자 인증, 미설정 시 DISABLED). 서버 대역 시험 추가.

## 0.3.1 (2026-10-15)

- boundary-contracts v1.2.0. `HttpQuestionReader`로 우리 서버를 읽는 I1 서버 적합성 시험 추가(여러 쪽 목록, reviewed_only, 해시 재계산, NOT_FOUND, PERMISSION_DENIED, INTEGRITY_ERROR, 서버 중단 UNAVAILABLE). 서버 수정은 필요 없었다.

## 0.3.0 (2026-10-15) HTTP 읽기 면 (D49, D50)

- I1 HTTP 읽기 면(HTTP.md 표): 읽기 전용 토큰 `QA_READ_TOKENS`, 응답 스키마·해시 검증, 오류 코드 422 정정(UNSUPPORTED_VERSION, INTEGRITY_ERROR).
- 문진 스키마는 `openit-survey-schema`(survey-schema-v1.0.0) wheel로 설치. `QA_SURVEY_SCHEMA` 삭제, `build/survey-schema` 복사 방식 삭제.
- boundary-contracts v1.1.0. 이미지 빌드 스크립트 `scripts/build_wheels.sh`(두 태그를 git archive).
- 이미지: HEALTHCHECK, 라벨 `io.openit.*` 6개로 통일.
- 스모크에 읽기 호출과 헬스 확인 추가(포트는 Docker 배정).

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
