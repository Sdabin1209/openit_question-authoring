# 런북: question-authoring

## 개발 환경

```
python3 -m venv .venv && . .venv/bin/activate
pip install -e ../boundary-contracts jsonschema pytest && pip install -e . --no-deps
python -m pytest -q
```

스키마 위치는 기본으로 워크스페이스의 읽기 전용 폴더(`../boundary-contracts/schemas`, `../survey-service/schema`)를 쓴다. 다른 곳은 `Validators(boundary_schema_dir, survey_schema_dir)` 인자로 넘긴다.

## 이미지 스모크 시험 (D22)

```
scripts/smoke.sh                 # docker가 있을 때
DOCKER=docker.exe scripts/smoke.sh   # WSL에서 docker.exe만 있을 때
```

`vendor-contracts/`를 만들고(`scripts/vendor_contracts.sh`, 커밋하지 않음) 이미지를 빌드해 컨테이너를 띄운 뒤 `/healthz`, `/readyz`, 자료 등록 -> 미리보기 -> 확인, 인증 없는 요청 거부, 비root 실행, 라벨을 확인한다. 변경을 완료했다고 말하기 전에 돌린다.

## 설정(환경 변수. 진입점 `python -m question_authoring`만 읽는다)

| 이름 | 뜻 | 기본 |
| --- | --- | --- |
| QA_DB_PATH | SQLite 파일 | /data/questions.sqlite3 |
| QA_AUTHOR_TOKENS | `토큰:actor-id:역할` 쉼표 목록. 비밀 값 | 없음(모든 요청 403) |
| QA_BOUNDARY_SCHEMAS | boundary-contracts schemas 디렉터리 | 이미지: /opt/boundary-contracts/schemas |
| QA_SURVEY_SCHEMA | 문진 스키마 디렉터리 | 이미지: /opt/survey-schema |
| QA_HOST, QA_PORT | 바인드 | 0.0.0.0, 8080 |

로그는 표준 출력. 데이터는 `/data` 볼륨 하나.

## 흔한 상황

- 저자가 자료를 올렸는데 FALLBACK: 응답의 `reason`을 보고, 원본이 텍스트 양식인지 확인한다. 화면 입력(`/edits`)으로 이어서 등록할 수 있다. 원본 해시는 출처로 남는다.
- 확인이 `FAILED_PRECONDITION`: (a) 열린 플래그가 남음(응답 `details.flag_ids`), (b) 미리보기 이후 초안이 바뀜(미리보기를 다시 받는다), (c) 초안을 만든 뒤 새 버전이 생김(최신에서 다시 초안), (d) 이전 버전과 내용이 같음.
- `INTEGRITY_ERROR`: 해당 버전의 저장 내용이 기록된 해시와 다르다. 파일을 변조했거나 손상된 것이다. 백업에서 복원한다. 버전을 고쳐 쓰지 않는다.
- 승인 철회: `POST /v1/reviews`로 REJECTED 또는 CHANGES_REQUESTED(사유 필수)를 덧붙인다. 그 버전은 `reviewed_only`에서 빠진다. 다시 승인하려면 APPROVED + clinical_basis_approved를 덧붙인다.
- 백업: 서비스를 멈추거나 `sqlite3 questions.sqlite3 ".backup out.sqlite3"`로 복사한다. 복원 후 `SqliteQuestionStore`로 열어 각 버전을 `get_question_bank`로 읽어 해시를 확인한다.
- 새 추출기 추가: `extractors/base.py`의 `Extractor` 인터페이스를 구현하고 `default_registry()`에 등록한다. 시험은 `tests/test_extractor.py`를 본뜬다. 새 추출기는 추정하지 않는다는 규칙(UNSURE/MISSING/UNPARSED)을 지켜야 한다.
