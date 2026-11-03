"""통신(보내는 쪽): release-gate 의 판정(I4 find_decisions → GateDecision)을 읽어 저작 화면에 보인다. 입력 DecisionsQuery -> 출력 Decisions(계약).

이웃과의 HTTP 는 계약 클라이언트(boundary_contracts.clients.HttpReleaseReader)만 쓴다. 읽기만 한다: 후보 제출은 이 영역의 권한이 아니다(DATA_OWNERSHIP, INTERFACES I5).
영역 밖 호출은 자기 영역 게이트웨이 출구로만 나간다(RUNTIME R1.8). 받은 판정은 다시 검증하지 않고 그대로 낸다(ADR 0010 R7: 클라이언트가 읽기 모드로 이미 확인했다).
"""
from __future__ import annotations

from typing import Optional

from boundary_contracts.clients import HttpReleaseReader
from boundary_contracts.errors import BoundaryError


def http_gate_source(url: str, token: str, timeout: float, egress_proxy: Optional[str] = None) -> HttpReleaseReader:
    return HttpReleaseReader(url, token, timeout=timeout, egress_proxy=egress_proxy)


class GateReader:
    INPUT_SCHEMA = "AuthoringRequests#DecisionsQuery"
    OUTPUT_SCHEMA = "AuthoringResponses#Decisions"

    def __init__(self, source):
        self.source = source          # find_decisions 를 가진 읽기 클라이언트(http_gate_source 또는 시험 대역)

    def run(self, query: dict) -> dict:
        """query: DecisionsQuery(question_set_id 필수, version·cursor·limit 선택, 값은 문자열). 최신부터의 GateDecision 목록."""
        ver = query.get("version")
        try:
            items, nxt = self.source.find_decisions(question_set={"id": query["question_set_id"], "version": ver},
                                                    cursor=query.get("cursor"), limit=int(query.get("limit", "50")))
        except BoundaryError as e:
            if e.code in ("INTEGRITY_ERROR", "UNSUPPORTED_VERSION", "INVALID_RECORD"):      # 상류의 깨진 응답·요청 거절은 성공으로 취급하지 않는다(FAULT_POLICY 10)
                raise BoundaryError("UNAVAILABLE", "release-gate 의 응답이 올바르지 않다") from None
            raise
        return {"items": items, "next_cursor": nxt}
