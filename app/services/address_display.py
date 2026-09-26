"""Normalisation d'adresses fournisseur pour affichage (texte sûr, sans HTML)."""

from __future__ import annotations

import re
from typing import Any

_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t]+")


def strip_supplier_html(raw: str | None) -> str:
    """Supprime les balises HTML fournisseur ; <br> → saut de ligne. Jamais de HTML rendu."""
    if raw is None:
        return ""
    text = _BR_RE.sub("\n", str(raw))
    text = _TAG_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_SPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def street_from_address_data(address_data: dict[str, Any] | None) -> str | None:
    """Extrait la rue structurée (string ou liste Magento)."""
    if not isinstance(address_data, dict):
        return None
    street = address_data.get("street")
    if isinstance(street, list):
        parts = [str(p).strip() for p in street if p is not None and str(p).strip()]
        return ", ".join(parts) if parts else None
    if street is None:
        return None
    text = str(street).strip()
    return text or None


def retailer_address_fields(
    *,
    raw_address: str | None,
    address_data: dict[str, Any] | None,
) -> tuple[str | None, str | None, str | None]:
    """(address, city, postcode) propres pour AgencyData / UI.

    Préfère street + city + postcode structurés ; sinon assainit le HTML `address`.
    """
    data = address_data if isinstance(address_data, dict) else {}
    street = street_from_address_data(data)
    city = str(data.get("city") or "").strip() or None
    postcode = str(data.get("postcode") or "").strip() or None

    if street:
        return street, city, postcode

    cleaned = strip_supplier_html(raw_address)
    if not cleaned:
        return None, city, postcode

    # Si le blob HTML contient déjà ville/CP, ne garder que la 1re ligne utile
    # comme rue lorsque city/postcode structurés sont présents.
    lines = cleaned.split("\n")
    if city or postcode:
        return lines[0], city, postcode
    return cleaned.replace("\n", ", "), city, postcode


def format_agency_address_lines(
    *,
    address: str | None,
    postal_code: str | None = None,
    city: str | None = None,
    country: str | None = None,
) -> list[str]:
    """Lignes d'adresse affichables sans duplication CP/ville."""
    cleaned = strip_supplier_html(address)
    lines = [line for line in cleaned.split("\n") if line.strip()]
    postal = (postal_code or "").strip()
    city_name = (city or "").strip()
    city_line = " ".join(p for p in (postal, city_name) if p).strip()

    blob = " ".join(lines).casefold()
    postal_cf = postal.casefold()
    city_cf = city_name.casefold()
    has_postal = bool(postal_cf) and postal_cf in blob
    has_city = bool(city_cf) and city_cf in blob

    if city_line and not (has_postal and has_city):
        if has_postal and not has_city and city_name:
            # CP déjà dans une ligne : n'ajouter que la ville si absente — rare
            if city_cf not in blob:
                lines.append(city_name.title() if city_name.isupper() else city_name)
        elif has_city and not has_postal and postal:
            if postal_cf not in blob:
                lines.append(postal)
        else:
            display_city = city_name.title() if city_name.isupper() else city_name
            display_line = " ".join(p for p in (postal, display_city) if p)
            lines.append(display_line)

    country_name = (country or "").strip()
    if country_name:
        country_cf = country_name.casefold()
        if country_cf not in blob and not any(country_cf == ln.casefold() for ln in lines):
            lines.append(country_name)

    # Dédupliquer lignes exactes (insensible à la casse / espaces)
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = _SPACE_RE.sub(" ", line).strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(_SPACE_RE.sub(" ", line).strip())
    return out


def format_agency_address_text(
    *,
    address: str | None,
    postal_code: str | None = None,
    city: str | None = None,
    country: str | None = None,
) -> str:
    return "\n".join(
        format_agency_address_lines(
            address=address,
            postal_code=postal_code,
            city=city,
            country=country,
        )
    )
