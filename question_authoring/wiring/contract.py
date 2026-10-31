"""경계 계약 스키마(boundary-contracts, D107 승격)로 진입점의 환경 변수와 purge 출력을 배포 인터페이스 계약으로 검사한다(요청·응답·ext는 각각 서버 면과 컴포넌트 출력 스키마가 검사한다).

왜 한 곳: 손으로 키와 형식을 검사하던 코드는 스키마와 어긋나도 아무것도 깨지지 않았다. 계약 스키마가 정본이고
이 모듈은 이름으로 검증기를 찾아 쓰기만 한다(스키마 사본을 두지 않는다, 워크스페이스 규칙).
오류 메시지에는 값이 아니라 위치와 규칙 이름만 쓴다: 요청 본문·환경 변수 값에 자료나 비밀이 섞일 수 있다.
"""
from __future__ import annotations

from typing import Any, Optional

import boundary_contracts

_cache: dict = {}


def _validator(schema: str, def_name: Optional[str]):
    key = (schema, def_name)
    if key not in _cache:
        v = boundary_contracts.validator(schema)
        # $defs 하나를 루트로 쓴다. 같은 검증기(resolver)를 이어받아 _common 참조도 풀린다
        _cache[key] = v.evolve(schema={"$ref": f"#/$defs/{def_name}"}) if def_name else v
    return _cache[key]


def problems(schema: str, def_name: Optional[str], value: Any) -> list[str]:
    out = []
    for e in sorted(_validator(schema, def_name).iter_errors(value), key=lambda e: list(map(str, e.absolute_path))):
        out.append("/" + "/".join(map(str, e.absolute_path)) + f": {e.validator} 규칙 위반")
    return out


def environment_problems(env: dict) -> list[str]:
    """QuestionAuthoringDeployInterface.Environment로 QA_ 환경 변수를 검사한다. 이 서비스와 무관한 변수는 스키마가 무시한다."""
    return problems("QuestionAuthoringDeployInterface", "Environment", env)
