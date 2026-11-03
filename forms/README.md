# 운영 문진표 양식

| 파일 | 문진표 ID | 제목 | 문항 순서 |
| --- | --- | --- | --- |
| `health-screening-v1.txt` | `health-screening` | 건강검진 문진표 | Q1.D01 ... Q2.D05, Q3, Q4 ... Q10 |
| `health-screening-short-order.txt` | `health-screening-b` | 건강검진 문진표(순서 B) | Q3, Q4 ... Q10, 그다음 Q1.D01 ... Q2.D05 |

두 양식은 문항 31개와 칸 구조가 같고 순서(`순서:`)와 ID·제목만 다르다.

## 출처
- 문항 ID와 질문 문장: 기존 데이터셋의 문항 은행(`question_bank.json`)의 `official` 문장. 사용자 지시(기존 데이터셋에서 추론).
- 칸 구성·순서·자료형·값 단위·최소/최대·선택지·선행 조건 7개·후속 문항 관계: survey-service 시험용 문항 세트(`sample_bank.py`)와 그것이 읽는 52칸 규격.
- 임상 값이 아닌 것(저자가 정해야 함, 이 파일은 임시 값): 거절 설명, 다시 묻는 문장, 정책 한도(3/2/2), 세션 설정의 한도와 고정 문장, 위험 문구. 위험 기준은 모두 `없음`이다. 임상 검토는 이 영역이 하지 않는다(D30).

## 올리는 절차
1. 저작 API로 올린다: `POST /v1/materials?question_set_id=<문진표 ID>&filename=<파일명>`에 파일 본문. `question_set_id`는 파일 머리의 `문진표 ID:`와 같아야 한다(다르면 거절).
2. `GET /v1/drafts/{id}/preview`로 미리보기를 보고, 열린 플래그가 없고 `validation_errors`가 비어 있는지 확인한다(이 두 양식은 시험에서 플래그 없이 추출된다).
3. 저자가 `POST /v1/drafts/{id}/confirm`으로 확인하면 버전 1이 된다. 4-eyes를 켠 구성이면 다른 검수자의 승인이 더 필요하다.
시험: `tests/test_prerequisite.py`.
