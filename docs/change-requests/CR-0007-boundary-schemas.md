# CR-0007 영역 경계를 넘는 형식의 스키마 초안

- 상태: **처리됨**. 이 영역이 낸 스키마 5개가 계약 boundary-contracts 2.2.0(D107)으로 승격됐고, U49(2026-10-30)에서 이 폴더의 사본을 지웠다. 정본은 계약 패키지다(`boundary_contracts.load_schema(<이름>)`, `validator(<이름>)`). 예시와 거부 예시는 패키지의 `conformance/examples`, `conformance/examples_invalid`, 출처 매니페스트는 `conformance/promoted/question-authoring.MANIFEST.json`.
- 근거(당시): `docs/audit/structure-rules-2026-10-28.md` QA-16, QA-12. 내용은 git 기록(`docs/change-requests/schemas/`, `contract-ready/`)에 남아 있다.

## 분류 (U49)

| 지운 사본(경로 `docs/change-requests/...`) | 계약의 정본 | 분류 | 처리 | 근거 |
| --- | --- | --- | --- | --- |
| `schemas/authoring-requests.schema.json`, `contract-ready/schemas/AuthoringRequests.schema.json` | `AuthoringRequests` | (가) 승격된 사본 | 지움 | 계약에 같은 이름. 코드는 `question_authoring/contract.py`로 계약을 읽는다 |
| `schemas/authoring-responses.schema.json`, `contract-ready/schemas/AuthoringResponses.schema.json` | `AuthoringResponses` | (가) | 지움 | 같음 |
| `schemas/signals-response.schema.json`, `contract-ready/schemas/SignalsResponse.schema.json` | `SignalsResponse` | (가) | 지움 | 같음 |
| `schemas/ext-question-authoring.schema.json`, `contract-ready/schemas/QuestionAuthoringExt.schema.json` | `QuestionAuthoringExt` | (가) | 지움 | 같음 |
| `schemas/deploy-interface.schema.json`, `contract-ready/schemas/QuestionAuthoringDeployInterface.schema.json` | `QuestionAuthoringDeployInterface` | (가) | 지움 | 같음 |
| `schemas/examples/*.json`(45), `contract-ready/examples/{valid,invalid}/*.json` | 패키지 `conformance/examples`, `examples_invalid` | (가) | 지움 | 계약 패키지에 같은 예시가 있고 시험은 그것을 읽는다(`tests/test_contract_u3.py` 등) |
| `schemas/check.py`, `contract-ready/check.py`, `contract-ready/MANIFEST.json` | 패키지 `conformance/promoted/question-authoring.MANIFEST.json` | (가) | 지움 | 사본 검증기. 계약 쪽 verify.py와 이 영역 시험이 대신한다 |

(나)(계약에 없는 것)는 없었다. 이 영역 안에서만 쓰는 형식(텍스트 양식 문법 등)은 이 폴더에 스키마 파일이 아니라 `docs/design.md` 7절과 ADR 0004·0018에 있다.

남긴 것: 이 CR 문서와 CR-0001~0006(결정 기록).
