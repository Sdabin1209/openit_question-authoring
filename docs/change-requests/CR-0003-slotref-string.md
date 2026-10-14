# CR-0003 ChangeClassification.affected_slots의 SlotRef 문자열 형식

- 상태: 헤드가 승인함(boundary-contracts v1.0.0, INTERFACES.md/CONVENTIONS.md 10절 반영).

- 대상: ChangeClassification 스키마("문진 스키마 SlotRef를 문자열로 직렬화한 값")
- 부족한 점: 직렬화 형식이 정해져 있지 않다. 예시는 `Q3b.frequency`다. SlotRef에는 `repeat_key`가 있으나 QuestionBank의 정의에는 반복 키 값이 없다(정의 수준의 변경 분류).
- 현재 처리: `"<question_id>.<slot_id>"`. `repeat_key`는 쓰지 않는다.
- 요청: 소비자(합성 데이터, model-lab)가 기대하는 형식을 확정해 `CONVENTIONS.md`나 스키마 설명에 적어 달라.
