"""통신(보내는 쪽): ops-feedback의 실패 사례(I7)를 읽어 개선 신호의 재료로 넘긴다. 입력 FailureReadRequest -> 출력 FailureBundle.

계약 클라이언트(boundary_contracts.clients.HttpFailureStore)만 쓴다. 담는 것은 사례와 비식별 trace뿐이다.
상류 장애·인증 실패를 "trace 없음"으로 조용히 세지 않는다(FAULT_POLICY 9·10): UNAVAILABLE·PERMISSION_DENIED는 그대로 올린다.
"""
from __future__ import annotations

import time
from typing import Optional

from boundary_contracts.clients import HttpFailureStore
from boundary_contracts.errors import BoundaryError

INPUT_SCHEMA = "FailureReadRequest"
OUTPUT_SCHEMA = "FailureBundle"


class FailureReader:
    def __init__(self, source=None, *, url: Optional[str] = None, token: str = "", timeout: float = 5.0):
        """source가 있으면(메모리 구현·시험) 그것을 쓰고, 없으면 url의 HttpFailureStore를 만든다."""
        self.source = source if source is not None else HttpFailureStore(url, token, timeout=timeout)

    def run(self, inp: dict) -> dict:
        """deadline_seconds: 이 호출 전체의 상한(호출 하나의 timeout은 클라이언트가 가진다). 넘으면 부분 결과를 내지 않고 UNAVAILABLE."""
        dl = inp["deadline_seconds"]
        t_end = None if dl is None else time.monotonic() + dl

        def check_deadline():
            if t_end is not None and time.monotonic() > t_end:
                raise BoundaryError("UNAVAILABLE", "개선 신호 읽기가 마감 시간을 넘었다(ops-feedback이 느리다)")
        mx = inp["max_cases"]
        cases, cur, truncated = [], None, False
        while True:
            check_deadline()
            items, cur = self.source.list_cases(review_status=inp["review_status"], latest_only=True, cursor=cur, limit=min(500, mx))
            cases += items
            if len(cases) >= mx:
                truncated = cur is not None or len(cases) > mx
                cases = cases[:mx]
                break
            if cur is None:
                break
        traces: dict = {}
        for c in cases:
            ref = c.get("question_set")
            if not isinstance(ref, dict) or "id" not in ref:
                continue
            if inp["question_set_id"] is not None and ref["id"] != inp["question_set_id"]:
                continue
            tref = c.get("deidentified_trace_ref")
            if tref in traces:
                continue
            check_deadline()
            try:
                traces[tref] = self.source.get_trace(tref)
            except BoundaryError as e:
                if e.code in ("UNAVAILABLE", "PERMISSION_DENIED"):
                    raise
                traces[tref] = None     # 읽지 못한 trace
        return {"cases": cases, "truncated": truncated, "traces": traces}
FailureReader.INPUT_SCHEMA = INPUT_SCHEMA
FailureReader.OUTPUT_SCHEMA = OUTPUT_SCHEMA
