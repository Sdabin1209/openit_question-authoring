"""추출기 인터페이스. 형식마다 추출기 하나를 레지스트리에 붙인다(D33).

추출기가 하는 일은 원본 바이트를 QuestionBank 모양의 초안과 플래그로 바꾸는 것까지다. 확신하지 못하면 값을
채우지 않거나 원문을 그대로 두고 플래그를 단다. 어떤 추출기든 결과는 같은 미리보기와 저자 확인을 거친다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

# 플래그 종류
MISSING = "MISSING"      # 자료에 없어서 비워 둠
UNSURE = "UNSURE"        # 읽었으나 확신하지 못함(값은 추정하지 않고 비우거나 원문을 둔다)
UNPARSED = "UNPARSED"    # 해석하지 못한 줄(버리지 않고 알린다)


class ExtractionFailure(Exception):
    """파싱할 수 없다. 서비스는 이것을 받으면 화면 입력 폴백으로 이어 가게 한다."""


@dataclass
class ExtractedFlag:
    path: str                # bank 안의 JSON 포인터
    kind: str
    message: str
    raw: Optional[str] = None


@dataclass
class ExtractionResult:
    bank: dict                                   # version이 없는 QuestionBank 모양(불완전할 수 있다)
    flags: list[ExtractedFlag] = field(default_factory=list)


@runtime_checkable
class Extractor(Protocol):
    name: str
    version: str
    extensions: tuple[str, ...]

    def can_handle(self, filename: str, data: bytes) -> bool: ...
    def extract(self, data: bytes, filename: str) -> ExtractionResult: ...


class ExtractorRegistry:
    def __init__(self, extractors: Optional[list[Extractor]] = None):
        self._items: list[Extractor] = []
        for e in extractors or []:
            self.register(e)

    def register(self, extractor: Extractor) -> None:
        if any(e.name == extractor.name for e in self._items):
            raise ValueError(f"이미 등록된 추출기: {extractor.name}")
        self._items.append(extractor)

    def names(self) -> list[str]:
        return [e.name for e in self._items]

    def select(self, filename: str, data: bytes, name: Optional[str] = None) -> Extractor:
        if name is not None:
            for e in self._items:
                if e.name == name:
                    return e
            raise ExtractionFailure(f"알 수 없는 추출기: {name}")
        for e in self._items:
            if e.can_handle(filename, data):
                return e
        raise ExtractionFailure(f"지원하지 않는 형식이다: {filename}")
