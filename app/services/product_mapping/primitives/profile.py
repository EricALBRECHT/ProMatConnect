"""Primitive « profil métallique » — R48/R70/R90/R100, M48/M70/M90, F45.

Les codes compacts type M4835 sont ramenés à leur profil (M48).
"""

from __future__ import annotations

import re

from app.services.product_mapping.primitives.textutil import fold

KNOWN_PROFILES = frozenset(
    {"R48", "R70", "R90", "R100", "M48", "M70", "M90", "F45"}
)

_PROFILE_RE = re.compile(
    r"\b(?P<p>"
    r"R\s*48|R\s*70|R\s*90|R\s*100|"
    r"M\s*48|M\s*70|M\s*90|"
    r"F\s*45|"
    r"M48\d{2}"  # ex. M4835 → M48
    r")\b",
    re.I,
)
_RAIL_DE_N_RE = re.compile(r"\brail\s+de\s+(?P<n>48|70|90|100)\b", re.I)
_MONTANT_N_RE = re.compile(r"\bmontant\s+(?P<n>48|70|90)\b", re.I)
_FOURRURE_N_RE = re.compile(
    r"\bfourrure\s+(?:profilee\s+)?(?:galvanisee?\s+)?(?P<n>45)\b", re.I
)


def normalize_profile(raw: str) -> str | None:
    t = re.sub(r"\s+", "", raw.upper())
    if t.startswith("M48") and len(t) > 3 and t[3:].isdigit():
        return "M48"
    if t in KNOWN_PROFILES:
        return t
    return None


def extract_metal_profile(text: str) -> str | None:
    """Profil normalisé, ou None si absent / illisible."""
    m = _PROFILE_RE.search(text)
    if m:
        return normalize_profile(m.group("p"))
    m = _RAIL_DE_N_RE.search(text)
    if m:
        return f"R{m.group('n')}"
    m = _MONTANT_N_RE.search(fold(text))
    if m:
        return f"M{m.group('n')}"
    m = _FOURRURE_N_RE.search(fold(text))
    if m:
        return "F45"
    return None
