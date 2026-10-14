from .base import (MISSING, UNPARSED, UNSURE, ExtractedFlag, ExtractionFailure, ExtractionResult, Extractor,
                   ExtractorRegistry)
from .textform import TextFormExtractor


def default_registry() -> ExtractorRegistry:
    return ExtractorRegistry([TextFormExtractor()])


__all__ = ["MISSING", "UNPARSED", "UNSURE", "ExtractedFlag", "ExtractionFailure", "ExtractionResult", "Extractor",
           "ExtractorRegistry", "TextFormExtractor", "default_registry"]
