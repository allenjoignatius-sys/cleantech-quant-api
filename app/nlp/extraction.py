"""
Resilient, provenance-aware extraction of structured metrics from unstructured
scientific abstracts and market news. See package docstring for the design.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Provenance + value containers
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class SourceRef:
    """Where a datapoint came from — enables traceable hyperlinking in the UI."""
    url: Optional[str] = None
    doi: Optional[str] = None
    title: Optional[str] = None
    source_type: Optional[str] = None  # academic_paper | news | patent ...

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class ExtractedValue:
    field: str               # canonical metric name, e.g. overpotential_mv
    value: float
    unit: str
    raw_text: str            # the exact substring matched
    start: int               # char offset in source text
    end: int
    confidence: float        # 0..1
    method: str              # "rule" | "llm" | "llm+rule"
    source: SourceRef = field(default_factory=SourceRef)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source"] = self.source.to_dict()
        d["value"] = round(self.value, 6)
        return d


@dataclass
class ExtractionResult:
    kind: str                              # "catalyst_metrics" | "fid_signals"
    values: List[ExtractedValue] = field(default_factory=list)
    flags: Dict[str, bool] = field(default_factory=dict)   # e.g. {"is_fid_related": True}
    source: SourceRef = field(default_factory=SourceRef)
    methods_used: List[str] = field(default_factory=list)

    def as_field_map(self) -> Dict[str, ExtractedValue]:
        """Highest-confidence value per field."""
        best: Dict[str, ExtractedValue] = {}
        for v in self.values:
            if v.field not in best or v.confidence > best[v.field].confidence:
                best[v.field] = v
        return best

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "values": [v.to_dict() for v in self.values],
            "flags": self.flags,
            "source": self.source.to_dict(),
            "methods_used": self.methods_used,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Quantity grammar — the robust numeric layer
# ──────────────────────────────────────────────────────────────────────────────
def normalize_text(text: str) -> str:
    """Normalise unicode so the grammar sees ASCII-ish numbers/units."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    # unify minus signs and dashes used in ranges
    t = (t.replace("−", "-")   # minus sign
          .replace("–", "-")   # en dash
          .replace("—", "-")   # em dash
          .replace("·", " ")   # middle dot (mA·cm-2)
          .replace(" ", " ")   # narrow nbsp
          .replace(" ", " "))  # nbsp
    t = t.replace("µ", "u").replace("μ", "u")
    # superscript minus-two for cm^-2
    t = t.replace("⁻²", "-2").replace("²", "2")
    return t


# number, optionally a range "a-b" or "a to b", optionally scientific notation
_NUM = r"[-+]?\d{1,4}(?:[.,]\d+)?(?:\s*[xX×]\s*10\^?-?\d+|[eE][-+]?\d+)?"
_RANGE = rf"(?P<lo>{_NUM})\s*(?:-|–|to)\s*(?P<hi>{_NUM})"
_SINGLE = rf"(?P<val>{_NUM})"


def _to_float(token: str) -> Optional[float]:
    token = token.strip().replace(",", ".")
    m = re.match(r"^([-+]?\d+(?:\.\d+)?)\s*[xX×]\s*10\^?(-?\d+)$", token)
    if m:
        return float(m.group(1)) * (10 ** int(m.group(2)))
    try:
        return float(token)
    except ValueError:
        return None


@dataclass
class Quantity:
    value: float
    raw: str
    start: int
    end: int
    is_range: bool = False


class QuantityParser:
    """Finds numeric quantities (incl. ranges) preceding a unit pattern."""

    def find(self, text: str, unit_pattern: str) -> List[Quantity]:
        out: List[Quantity] = []
        # range first (e.g. "250-300 mV"), then single
        rng = re.compile(rf"{_RANGE}\s*(?:{unit_pattern})", re.IGNORECASE)
        for m in rng.finditer(text):
            lo, hi = _to_float(m.group("lo")), _to_float(m.group("hi"))
            if lo is not None and hi is not None:
                out.append(Quantity((lo + hi) / 2.0, m.group(0), m.start(), m.end(), True))
        consumed = {(q.start, q.end) for q in out}
        single = re.compile(rf"{_SINGLE}\s*(?:{unit_pattern})", re.IGNORECASE)
        for m in single.finditer(text):
            if any(m.start() >= s and m.end() <= e for s, e in consumed):
                continue
            v = _to_float(m.group("val"))
            if v is not None:
                out.append(Quantity(v, m.group(0), m.start(), m.end(), False))
        return out


# ──────────────────────────────────────────────────────────────────────────────
# Rule-based domain extractors
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class _MetricSpec:
    field: str
    unit: str
    unit_pattern: str
    anchors: Tuple[str, ...]          # keywords that must appear nearby
    transform: Optional[Callable[[float, str], Tuple[float, str]]] = None
    plausible: Tuple[float, float] = (-1e9, 1e9)


def _volt_to_mv(v: float, raw: str) -> Tuple[float, str]:
    # If matched as volts (no 'm'), convert to mV; values <5 with a 'V' unit are volts.
    if re.search(r"\bm\s*V", raw, re.IGNORECASE):
        return v, "mV"
    return (v * 1000.0, "mV") if v < 5 else (v, "mV")


CATALYST_METRICS: List[_MetricSpec] = [
    _MetricSpec("overpotential_mv", "mV",
                r"m?V(?:\s*(?:vs\.?\s*RHE|@|at)\b)?",
                ("overpotential", "η", "eta", "overvoltage"),
                transform=_volt_to_mv, plausible=(10, 1500)),
    _MetricSpec("current_density_ma_cm2", "mA/cm2",
                r"(?:mA|A)\s*[\/ ]?\s*cm-?2",
                ("current density", "at", "@", "j ="), plausible=(0.01, 5000)),
    _MetricSpec("faradaic_efficiency_pct", "%",
                r"%",
                ("faradaic", "fe", "selectivity", "efficiency", "conversion", "yield"),
                plausible=(1, 100)),
    _MetricSpec("nh3_conversion_pct", "%",
                r"%",
                ("nh3 conversion", "ammonia conversion", "conversion of ammonia"),
                plausible=(1, 100)),
    _MetricSpec("durability_h", "h",
                r"(?:h|hr|hrs|hours)\b",
                ("durability", "stability", "stable", "operation", "continuous", "for"),
                plausible=(1, 200000)),
    _MetricSpec("temperature_c", "°C",
                r"(?:°?\s*C|degrees?\s*C|celsius)\b",
                ("temperature", "at", "°c", "operating"), plausible=(20, 1200)),
    _MetricSpec("tof_s", "s-1",
                r"s-?1",
                ("tof", "turnover frequency", "turnover"), plausible=(1e-6, 1e6)),
]


def _anchor_near(text_lower: str, anchors: Tuple[str, ...], start: int, window: int = 60) -> float:
    """Confidence boost if an anchor keyword sits within `window` chars of the match."""
    lo = max(0, start - window)
    ctx = text_lower[lo:start + window]
    return 0.9 if any(a in ctx for a in anchors) else 0.4


class RuleBasedExtractor:
    def __init__(self) -> None:
        self.parser = QuantityParser()

    def extract_catalyst_metrics(self, text: str, source: SourceRef) -> ExtractionResult:
        norm = normalize_text(text)
        low = norm.lower()
        values: List[ExtractedValue] = []
        for spec in CATALYST_METRICS:
            for q in self.parser.find(norm, spec.unit_pattern):
                value, unit = (spec.transform(q.value, q.raw) if spec.transform
                               else (q.value, spec.unit))
                if not (spec.plausible[0] <= value <= spec.plausible[1]):
                    continue
                conf = _anchor_near(low, spec.anchors, q.start)
                if conf < 0.5:
                    continue  # drop unanchored ambiguous matches (e.g. stray %)
                values.append(ExtractedValue(
                    field=spec.field, value=value, unit=unit, raw_text=q.raw,
                    start=q.start, end=q.end, confidence=conf, method="rule",
                    source=source,
                ))
        return ExtractionResult("catalyst_metrics", values, {}, source, ["rule"])

    def extract_fid_signals(self, text: str, source: SourceRef) -> ExtractionResult:
        norm = normalize_text(text)
        low = norm.lower()
        values: List[ExtractedValue] = []

        # capacity in MW / GW
        for q in self.parser.find(norm, r"GW"):
            values.append(ExtractedValue("capacity_mw", q.value * 1000.0, "MW", q.raw,
                                         q.start, q.end, 0.85, "rule", source))
        for q in self.parser.find(norm, r"MW"):
            values.append(ExtractedValue("capacity_mw", q.value, "MW", q.raw,
                                         q.start, q.end, 0.8, "rule", source))
        # electrolyzer capacity sometimes quoted in tonnes/day H2
        for q in self.parser.find(norm, r"(?:t|tonnes?|tpd)\s*(?:\/?\s*day|per day|\/d)?\s*(?:of\s*)?(?:H2|hydrogen)?"):
            if "tpd" in q.raw.lower() or "day" in q.raw.lower():
                values.append(ExtractedValue("capacity_tpd_h2", q.value, "tpd", q.raw,
                                             q.start, q.end, 0.6, "rule", source))
        # investment $ amounts
        money = re.compile(r"(?:US)?\$\s*(\d+(?:[.,]\d+)?)\s*(billion|bn|million|m)\b", re.IGNORECASE)
        for m in money.finditer(norm):
            amt = _to_float(m.group(1)) or 0.0
            mult = 1000.0 if m.group(2).lower() in ("billion", "bn") else 1.0
            values.append(ExtractedValue("investment_usd_millions", amt * mult, "USD_m",
                                         m.group(0), m.start(), m.end(), 0.8, "rule", source))

        is_fid = bool(re.search(
            r"\b(final investment decision|fid|reached fid|taken fid|greenlights?|"
            r"sanctioned|financial close)\b", low))
        return ExtractionResult("fid_signals", values, {"is_fid_related": is_fid},
                                source, ["rule"])


# ──────────────────────────────────────────────────────────────────────────────
# Optional structured-LLM backend
# ──────────────────────────────────────────────────────────────────────────────
LLMClient = Callable[[str], str]  # prompt -> raw JSON string


class LLMExtractor:
    """
    Structured-output LLM extractor. `client(prompt)` must return a JSON string
    shaped like ``{"values":[{"field","value","unit","quote","confidence"}], "is_fid_related":bool}``.
    Char offsets are recovered by locating each returned ``quote`` in the source text,
    preserving traceability. If the client is absent/erroring, returns no values.
    """

    CATALYST_FIELDS = {"overpotential_mv", "current_density_ma_cm2", "faradaic_efficiency_pct",
                       "nh3_conversion_pct", "durability_h", "temperature_c", "tof_s"}
    FID_FIELDS = {"capacity_mw", "capacity_tpd_h2", "investment_usd_millions"}

    def __init__(self, client: Optional[LLMClient] = None) -> None:
        self.client = client

    @property
    def available(self) -> bool:
        return self.client is not None

    def _run(self, kind: str, text: str, source: SourceRef, allowed: set) -> ExtractionResult:
        if not self.client:
            return ExtractionResult(kind, [], {}, source, [])
        prompt = self._build_prompt(kind, text, sorted(allowed))
        try:
            raw = self.client(prompt)
            data = json.loads(raw)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("LLM extraction failed, falling back to rules: %s", exc)
            return ExtractionResult(kind, [], {}, source, [])

        values: List[ExtractedValue] = []
        for item in data.get("values", []):
            f = item.get("field")
            if f not in allowed:
                continue
            try:
                val = float(item["value"])
            except (KeyError, TypeError, ValueError):
                continue
            quote = item.get("quote", "") or ""
            start = text.find(quote) if quote else -1
            end = start + len(quote) if start >= 0 else -1
            values.append(ExtractedValue(
                field=f, value=val, unit=item.get("unit", ""), raw_text=quote,
                start=start, end=end,
                confidence=float(item.get("confidence", 0.75)), method="llm",
                source=source,
            ))
        flags = {}
        if "is_fid_related" in data:
            flags["is_fid_related"] = bool(data["is_fid_related"])
        return ExtractionResult(kind, values, flags, source, ["llm"])

    def extract_catalyst_metrics(self, text: str, source: SourceRef) -> ExtractionResult:
        return self._run("catalyst_metrics", text, source, self.CATALYST_FIELDS)

    def extract_fid_signals(self, text: str, source: SourceRef) -> ExtractionResult:
        return self._run("fid_signals", text, source, self.FID_FIELDS)

    @staticmethod
    def _build_prompt(kind: str, text: str, fields: List[str]) -> str:
        return (
            "You are a precise data-extraction engine for an energy-transition "
            "intelligence platform. Extract ONLY the following fields if present: "
            f"{fields}. Return STRICT JSON: "
            '{"values":[{"field":..., "value": <number>, "unit":..., '
            '"quote":"<verbatim source span>", "confidence":0-1}], '
            '"is_fid_related": <bool>}. Use the exact verbatim quote so offsets can '
            f"be recovered.\n\nTEXT:\n{text}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────────────────────────────────────
class ResilientExtractor:
    """
    Prefers the LLM (if configured) but always backstops with the rule-based
    extractor and reconciles per field: an LLM value is kept; any rule value for a
    field the LLM missed is added (marked so), guaranteeing graceful degradation.
    """

    def __init__(self, llm_client: Optional[LLMClient] = None) -> None:
        self.rules = RuleBasedExtractor()
        self.llm = LLMExtractor(llm_client)

    def _reconcile(self, llm_res: ExtractionResult, rule_res: ExtractionResult) -> ExtractionResult:
        merged: List[ExtractedValue] = []
        llm_map = llm_res.as_field_map()
        rule_map = rule_res.as_field_map()
        methods: List[str] = []
        if llm_res.values:
            methods.append("llm")
        if rule_res.values:
            methods.append("rule")
        for f, v in llm_map.items():
            if f in rule_map:
                v.method = "llm+rule"
            merged.append(v)
        for f, v in rule_map.items():
            if f not in llm_map:
                merged.append(v)
        flags = {**rule_res.flags, **llm_res.flags}  # LLM flag wins if present
        return ExtractionResult(rule_res.kind, merged, flags, rule_res.source, methods or ["rule"])

    def extract_catalyst_metrics(self, text: str, source: Optional[SourceRef] = None) -> ExtractionResult:
        source = source or SourceRef()
        rule_res = self.rules.extract_catalyst_metrics(text, source)
        if self.llm.available:
            return self._reconcile(self.llm.extract_catalyst_metrics(text, source), rule_res)
        return rule_res

    def extract_fid_signals(self, text: str, source: Optional[SourceRef] = None) -> ExtractionResult:
        source = source or SourceRef()
        rule_res = self.rules.extract_fid_signals(text, source)
        if self.llm.available:
            return self._reconcile(self.llm.extract_fid_signals(text, source), rule_res)
        return rule_res


# ── Module-level convenience (rule-based unless a client is injected) ──────────
_default = ResilientExtractor()


def extract_catalyst_metrics(text: str, source: Optional[SourceRef] = None) -> ExtractionResult:
    return _default.extract_catalyst_metrics(text, source)


def extract_fid_signals(text: str, source: Optional[SourceRef] = None) -> ExtractionResult:
    return _default.extract_fid_signals(text, source)
