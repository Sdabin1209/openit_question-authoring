# 런북: question-authoring

## 개발 환경

```
python3 -m venv .venv && . .venv/bin/activate
pip install -e ../boundary-contracts -e ../survey-service/schema jsonschema pytest && pip install -e . --no-deps
python -m pytest -q
```

경계 계약 스키마는 `boundary_contracts`(v2.0.2), 문진 스키마는 `openit-survey-schema`(태그 survey-schema-v1.0.0) 패키지의 것을 쓴다. 복사본은 없다.

## 이미지 스모크 시험 (D22)

```
scripts/smoke.sh                 # docker가 있을 때
DOCKER=podman scripts/smoke.sh   # 실행 파일 이름만 바꿀 수 있다. 데몬에 못 닿으면 오류로 중단한다
```

`build/`에 계약 wheel을 만들고(`scripts/build_wheels.sh`: 두 태그 시점 소스(git archive)로 만든 wheel, 커밋하지 않음) 이미지를 빌드해 컨테이너를 띄운 뒤 `/healthz`, `/readyz`, 자료 등록 -> 미리보기 -> 확인, 인증 없는 요청 거부, 비root 실행, 라벨을 확인한다. 변경을 완료했다고 말하기 전에 돌린다.

## 비밀은 NAME_FILE로 (D84)

토큰 같은 비밀(QA_AUTHOR_TOKENS, QA_READ_TOKENS, QA_FAILURES_TOKEN)을 환경 변수 값으로 주면 `docker inspect`와 `docker exec env`에 평문이 보인다. 운영에서는 값을 파일에 두고 그 경로만 `NAME_FILE`로 준다(예: `-v /srv/secrets/qa:/run/secrets:ro -e QA_AUTHOR_TOKENS_FILE=/run/secrets/author_tokens`). 코드는 `boundary_contracts.read_secret(NAME)`으로 읽는다: `NAME_FILE`이 있으면 그 파일(앞뒤 공백 제거), 없을 때만 `NAME` 환경 변수(로컬 개발용)를 쓴다. 파일이 없거나 비면 시작을 거부하며 오류 메시지에는 경로만 있고 값은 없다. 비밀 값은 로그·`/readyz`·오류 본문에 쓰지 않으며 `tests/test_secrets.py`가 확인한다. 파일은 컨테이너 사용자(uid 10001)가 읽을 수 있어야 한다.

## 설정(환경 변수. 진입점 `python -m question_authoring`만 읽는다)

| 이름 | 뜻 | 기본 |
| --- | --- | --- |
| QA_DB_PATH | SQLite 파일 | /data/questions.sqlite3 |
| QA_READ_TOKENS(`_FILE` 가능) | `역할=토큰` 쉼표 목록. I1 읽기 전용 토큰(저작 경로는 403). 비밀 값 | 없음(읽기 면 모두 403) |
| QA_FAILURES_URL | ops-feedback(I7) URL. 비우면 `/v1/signals`는 DISABLED | 없음 |
| QA_FAILURES_TOKEN_ENV | I7 토큰의 비밀 이름. 이 이름의 `_FILE`(또는 개발용 환경 변수)을 읽는다 | QA_FAILURES_TOKEN |
| QA_AUTHOR_TOKENS(`_FILE` 가능) | `토큰:actor-id:역할` 쉼표 목록. 비밀 값 | 없음(모든 요청 403) |
| QA_HOST, QA_PORT | 바인드 | 0.0.0.0, 8080 |

로그는 표준 출력. 데이터는 `/data` 볼륨 하나.

## 흔한 상황

- 저자가 자료를 올렸는데 FALLBACK: 응답의 `reason`을 보고, 원본이 텍스트 양식인지 확인한다. 화면 입력(`/edits`)으로 이어서 등록할 수 있다. 원본 해시는 출처로 남는다.
- 확인이 `FAILED_PRECONDITION`: (a) 열린 플래그가 남음(응답 `details.flag_ids`), (b) 미리보기 이후 초안이 바뀜(미리보기를 다시 받는다), (c) 초안을 만든 뒤 새 버전이 생김(최신에서 다시 초안), (d) 이전 버전과 내용이 같음.
- `INTEGRITY_ERROR`: 해당 버전의 저장 내용이 기록된 해시와 다르다. 파일을 변조했거나 손상된 것이다. 백업에서 복원한다. 버전을 고쳐 쓰지 않는다.
- 승인 철회: `POST /v1/reviews`로 REJECTED 또는 CHANGES_REQUESTED(사유 필수)를 덧붙인다. 그 버전은 `reviewed_only`에서 빠진다. 다시 승인하려면 APPROVED + clinical_basis_approved를 덧붙인다.
- 백업: 서비스를 멈추거나 `sqlite3 questions.sqlite3 ".backup out.sqlite3"`로 복사한다. 복원 후 `SqliteQuestionStore`로 열어 각 버전을 `get_question_bank`로 읽어 해시를 확인한다.
- 새 추출기 추가: `extractors/base.py`의 `Extractor` 인터페이스를 구현하고 `default_registry()`에 등록한다. 시험은 `tests/test_extractor.py`를 본뜬다. 새 추출기는 추정하지 않는다는 규칙(UNSURE/MISSING/UNPARSED)을 지켜야 한다.

## 이미지 점검

라벨: `io.openit.service`, `io.openit.revision`, `io.openit.contracts.boundary`, `io.openit.contracts.survey-schema`, `io.openit.turn-engine`, `io.openit.model-runtime`(D50). HEALTHCHECK는 `/healthz`를 15초마다 부른다(`docker inspect -f '{{.State.Health.Status}}'`). 스모크는 읽기 토큰으로 I1 읽기 호출과 저작 경로 403도 확인한다.
