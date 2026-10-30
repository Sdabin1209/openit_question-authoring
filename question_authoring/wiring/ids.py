"""ID·시각 공급. 컴포넌트는 같은 입력이면 같은 출력을 내야 하므로(DESIGN 2절) 시각과 새 ID는 wiring이 만들어 입력으로 넘긴다."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone


def now_default() -> datetime:
    return datetime.now(timezone.utc)


def ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id_default(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"   # 왜: uuid4 전체(122비트 무작위, RFC 4122 4.4절). 64비트 절단의 이유 기록이 없었다(표준성 #18)
