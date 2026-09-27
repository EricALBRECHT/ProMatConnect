"""FeatureSet — porteur typé des caractéristiques extraites d'un SupplierProduct.

Règle absolue : une information absente vaut UNKNOWN (None). Elle n'est
JAMAIS convertie en False, 0 ou "" — sinon on fabrique des EXACT artificiels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

SOURCE_STRUCTURED = "structured"  # colonne fournisseur dédiée
SOURCE_FEATURE = "feature"  # attribut fournisseur clé/valeur
SOURCE_TEXT = "text"  # extrait de la désignation
SOURCE_UNKNOWN = "unknown"  # information absente


@dataclass
class FeatureValue:
    """Valeur unitaire + traçabilité de sa provenance."""

    raw: Any
    normalized: Any
    source: str = SOURCE_UNKNOWN

    @property
    def is_unknown(self) -> bool:
        return self.normalized is None


@dataclass
class FeatureSet:
    category_code: str | None
    values: dict[str, FeatureValue] = field(default_factory=dict)
    classified: bool = False
    confidence: float = 0.0
    extractor_version: str = ""
    reason: str | None = None

    def get(self, key: str, *, normalized: bool = True) -> Any:
        fv = self.values.get(key)
        if fv is None:
            return None
        return fv.normalized if normalized else fv.raw

    def as_attrs(self) -> dict[str, Any]:
        """Vue « plate » normalisée — format attendu par le matcher."""
        return {key: fv.normalized for key, fv in self.values.items()}

    def raw_attrs(self) -> dict[str, Any]:
        """Vue « plate » brute — valeurs telles qu'extraites, sans normalisation."""
        return {key: fv.raw for key, fv in self.values.items()}

    def sources(self) -> dict[str, str]:
        return {key: fv.source for key, fv in self.values.items()}


def feature_from_text(raw: Any) -> FeatureValue:
    """Valeur issue de la désignation ; None → UNKNOWN explicite."""
    if raw is None:
        return FeatureValue(raw=None, normalized=None, source=SOURCE_UNKNOWN)
    return FeatureValue(raw=raw, normalized=raw, source=SOURCE_TEXT)


def features_from_attrs(
    attrs: Mapping[str, Any], *, source: str = SOURCE_TEXT
) -> dict[str, FeatureValue]:
    out: dict[str, FeatureValue] = {}
    for key, value in attrs.items():
        if value is None:
            out[key] = FeatureValue(raw=None, normalized=None, source=SOURCE_UNKNOWN)
        else:
            out[key] = FeatureValue(raw=value, normalized=value, source=source)
    return out


def apply_normalizations(
    values: dict[str, FeatureValue], norms: Iterable[Any]
) -> dict[str, FeatureValue]:
    """Pose les clés normalisées sans jamais altérer la valeur source.

    Une valeur absente de la table de correspondance traverse inchangée ;
    une source UNKNOWN produit une cible UNKNOWN.
    """
    for spec in norms:
        source = values.get(spec.source_key)
        raw = source.normalized if source is not None else None
        normalized = None if raw is None else spec.mapping.get(raw, raw)
        source_kind = (
            SOURCE_UNKNOWN
            if normalized is None
            else (source.source if source is not None else SOURCE_TEXT)
        )
        values[spec.target_key] = FeatureValue(
            raw=raw, normalized=normalized, source=source_kind
        )
    return values


def feature_set_from_extraction(extraction: Any, *, norms: Iterable[Any] = ()) -> FeatureSet:
    """Adapte un ExtractionResult catégorie en FeatureSet, normalisations comprises."""
    values = features_from_attrs(getattr(extraction, "attributes", {}) or {})
    if norms:
        apply_normalizations(values, norms)
    return FeatureSet(
        category_code=getattr(extraction, "category_code", None),
        values=values,
        classified=bool(getattr(extraction, "classified", False)),
        confidence=float(getattr(extraction, "confidence", 0.0) or 0.0),
        extractor_version=getattr(extraction, "extractor_version", "") or "",
        reason=getattr(extraction, "reason", None),
    )
