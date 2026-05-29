"""
app.nlp — Resilient information-extraction pipeline.

Replaces brittle single-pattern regex scraping with a layered, provenance-aware
extractor:

    1. A robust *quantity grammar* that understands ranges, scientific notation,
       unicode minus/×, and unit variants (mV/V, mA·cm⁻²/A·cm⁻², %, h, °C, ppm).
    2. Keyword-anchored domain extractors for electrolyzer/catalyst metrics and
       for FID (Final Investment Decision) market signals.
    3. An optional structured-LLM backend that, when an API client is configured,
       returns the same typed schema and is reconciled against the rule-based
       result. Without a client it degrades gracefully to rules only.

Every extracted datapoint carries its source span (char offsets), the raw text,
a confidence, and the source URL/DOI — so the frontend can hard-link each number
back to the exact passage it came from (traceable hyperlinking).
"""
from app.nlp.extraction import (  # noqa: F401
    ExtractedValue,
    ExtractionResult,
    SourceRef,
    QuantityParser,
    RuleBasedExtractor,
    LLMExtractor,
    ResilientExtractor,
    extract_catalyst_metrics,
    extract_fid_signals,
)

__all__ = [
    "ExtractedValue",
    "ExtractionResult",
    "SourceRef",
    "QuantityParser",
    "RuleBasedExtractor",
    "LLMExtractor",
    "ResilientExtractor",
    "extract_catalyst_metrics",
    "extract_fid_signals",
]
