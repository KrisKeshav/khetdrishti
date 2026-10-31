"""Guardrails applied to every draft answer before the farmer sees it.

1. Numeric grounding: every number in the answer must match (within rounding) a number that a tool actually
   returned during this run, or one the farmer said. Small integers (0-16) are allowed as counts/day spans.
2. Dosage guardrail: an agro-chemical dose (e.g. "2 ml per litre") is allowed only if the same number appears
   in a retrieved agronomy document. Otherwise the agent must refer the farmer to the KVK / Kisan Call Centre.
"""
import re

NUM_RE = re.compile(r"(?<![\w])[-+]?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?")
DOSE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:ml|mL|g|gm|gram|grams|kg|l|litre|liter|litres|liters)\s*(?:/|per)\s*"
    r"(?:l|litre|liter|acre|ha|hectare|pump|tank|kg)", re.IGNORECASE)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
TAG_RE = re.compile(r"\[[^\]]{1,40}\]")
FREE_INTEGERS = set(range(0, 17))
KISAN_CALL_CENTRE = "1800-180-1551"


def _to_float(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def extract_numbers(text: str) -> list[float]:
    text = TAG_RE.sub(" ", text).replace(KISAN_CALL_CENTRE, " ")
    out = []
    for d in DATE_RE.findall(text):          # an ISO date contributes its parts
        out += [float(x) for x in d.split("-")]
    text = DATE_RE.sub(" ", text)
    for m in NUM_RE.findall(text):
        v = _to_float(m)
        if v is not None:
            out.append(abs(v))
    return out


def evidence_numbers(obj, acc: set | None = None) -> set:
    """Collect every number in a nested tool result (including numbers embedded in strings, e.g. dates)."""
    acc = set() if acc is None else acc
    if isinstance(obj, bool):
        return acc
    if isinstance(obj, (int, float)):
        acc.add(abs(float(obj)))
    elif isinstance(obj, str):
        for v in extract_numbers(obj):
            acc.add(v)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            evidence_numbers(k, acc)      # keys carry meaning too, e.g. "30d" in a price trend
            evidence_numbers(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            evidence_numbers(v, acc)
    return acc


def _supported(v: float, evidence: set) -> bool:
    if v.is_integer() and int(v) in FREE_INTEGERS:
        return True
    # Allows ordinary rounding (23.46 -> 23.5, 6811 -> 6800) but not changed values (6811 -> 7500).
    tol = lambda e: max(0.51, 0.015 * e)  # noqa: E731
    return any(abs(v - e) <= tol(e) for e in evidence)


def verify(answer: str, evidence: set, knowledge_texts: list[str]) -> dict:
    nums = extract_numbers(answer)
    unsupported = sorted({n for n in nums if not _supported(n, evidence)})
    kb_numbers = evidence_numbers(knowledge_texts)
    dose_violations = []
    for m in DOSE_RE.finditer(answer):
        v = _to_float(m.group(1))
        if v is None or not any(abs(v - k) < 1e-6 for k in kb_numbers):
            dose_violations.append(m.group(0))
    return {
        "passed": not unsupported and not dose_violations,
        "numbers_checked": len(nums),
        "unsupported_numbers": [int(n) if n.is_integer() else n for n in unsupported],
        "dose_violations": dose_violations,
    }


def strip_unsupported(answer: str, report: dict) -> str:
    """Last resort after failed self-correction: drop sentences containing unverifiable numbers or doses."""
    bad = set(report["unsupported_numbers"])
    sentences = re.split(r"(?<=[.!?।])\s+|\n", answer)
    kept = []
    for s in sentences:
        nums = {int(n) if n.is_integer() else n for n in extract_numbers(s)}
        if nums & bad or any(d in s for d in report["dose_violations"]):
            continue
        kept.append(s)
    text = " ".join(k for k in kept if k.strip())
    return text + " (Some figures were removed because they could not be verified against live data.)"
