"""표준성 점검 결정표 2절 #18 재현 시험.
생성 ID는 uuid4 전체 hex 32자(122비트 무작위)다. 기대값 근거: RFC 4122 4.4절(version 4 UUID는 122비트가 무작위)."""
import re
import uuid

from question_authoring.service import _new_id_default


def test_generated_ids_are_full_uuid4_hex():
    i = _new_id_default("dr")
    m = re.fullmatch(r"dr-([0-9a-f]{32})", i)
    assert m, i
    assert uuid.UUID(m.group(1)).version == 4
    assert len({_new_id_default("rv") for _ in range(1000)}) == 1000
