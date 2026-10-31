"""요청 전체 마감(D120, ADR 0025): 사람용(gateway를 지나는) 요청마다 서버 쪽 마감을 두고, 각 DB 문장은 남은 시간을 상한으로 쓴다.

왜: gateway가 응답 머리글을 기다리는 상한(response_header 13초)보다 서버의 최악이 길면 서버는 계속 일하는데 화면은 504를 받는다.
표준: 마감 전파(deadline propagation, gRPC deadline과 같은 생각). 시계는 주입한다(U422): 이 모듈은 시간을 직접 읽지 않고 clock()을 받는다.
요청 하나의 마감은 컨텍스트 변수에 둔다: 스레드마다(waitress 처리 스레드마다) 따로 이어지고, 서비스·저장소의 모든 메서드 시그니처를 바꾸지 않는다.
"""
from __future__ import annotations

import contextlib
import contextvars
from typing import Callable, Optional

from boundary_contracts.errors import BoundaryError

# 근거: gateway 응답 머리글 상한 13초(deploy ADR 0048, D119) - 여유 1초. D120. 이 PC 8MiB 삽입 실측 0.09초(ADR 0003·0007)로 정상 요청은 훨씬 안이다.
DEFAULT_REQUEST_DEADLINE_SECONDS = 12.0


class RequestDeadline:
    def __init__(self, clock: Callable[[], float], seconds: float = DEFAULT_REQUEST_DEADLINE_SECONDS):
        self._clock = clock
        self.seconds = float(seconds)
        self._at: contextvars.ContextVar[Optional[float]] = contextvars.ContextVar("qa_request_deadline", default=None)

    @contextlib.contextmanager
    def scope(self):
        """요청 시작: 마감 시각을 정한다. 바깥 범위가 이미 있으면 그대로 둔다(중첩 호출이 마감을 늘리지 못한다)."""
        if self._at.get() is not None:
            yield
            return
        token = self._at.set(self._clock() + self.seconds)
        try:
            yield
        finally:
            self._at.reset(token)

    def remaining(self) -> Optional[float]:
        """남은 초. 요청 범위 밖이면 None(마감 없음: 잡·시험의 직접 호출)."""
        at = self._at.get()
        return None if at is None else at - self._clock()

    def check(self, what: str) -> None:
        """남은 시간이 0 이하이면 더 진행하지 않는다(503 UNAVAILABLE, 재시도 가능). 호출하는 곳은 쓰기 직전이라 반쯤 쓴 상태가 없다."""
        r = self.remaining()
        if r is not None and r <= 0:
            raise BoundaryError("UNAVAILABLE", f"요청 전체 마감을 넘었다: {what}")
