"""Import catalogue Gedimat → SupplierProduct. Aucune Offer.

Un tellus_variant = un SupplierProduct, quel que soit le magasin.
Les variantes objectID du même tellus_variant sont fusionnées.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.gedimat.connector import CONNECTOR_KEY, SUPPLIER_NAME
from app.connectors.url_safety import sanitize_http_url
from app.models import Supplier, SupplierImport, SupplierProduct
from app.models.product_mapping import CORRECTION_SOURCES_PROTECTED, SupplierProductFeature
from app.services.units import ALLOWED_PRODUCT_UNITS, normalize_unit

CATALOG_SOURCE_KEY = "api:GEDIMAT:catalog"
FEATURE_CATEGORY = "GEDIMAT_CATALOG"
FEATURE_VERSION = "gedimat.catalog.v1"

_UNIT_BY_CODE = {
    "PIECE": ("pièce", "pièce"),
    "M2": ("m²", "m²"),
    "ML": ("m", "m"),
    "BOITE": ("boîte", "boîte"),
    "LOT": ("lot", "pièce"),
}


@dataclass
class GedimatImportResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deactivated: int = 0
    variants: int = 0
    collapsed_object_ids: int = 0
    supplier_id: int | None = None
    errors: list[str] = field(default_factory=list)


def load_catalog_hits(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    products = payload.get("products") if isinstance(payload, dict) else payload
    if not isinstance(products, list):
        raise ValueError("Export Gedimat illisible : liste products absente.")
    return [row for row in products if isinstance(row, dict)]


def group_by_tellus_variant(hits: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for hit in hits:
        sku = str(hit.get("tellus_variant") or "").strip()
        if not sku:
            continue
        grouped.setdefault(sku, []).append(hit)
    return grouped


def resolve_gedimat_supplier(session: Session) -> Supplier:
    """Fournisseur réel GEDIMAT. Ne réutilise jamais GEDIMAT TEST."""
    existing = session.scalar(select(Supplier).where(Supplier.name == SUPPLIER_NAME))
    if existing is not None:
        return existing
    supplier = Supplier(name=SUPPLIER_NAME, source_type="api", source_key=CONNECTOR_KEY)
    session.add(supplier)
    session.flush()
    return supplier


def import_gedimat_catalog(session: Session, hits: list[dict]) -> GedimatImportResult:
    result = GedimatImportResult()
    grouped = group_by_tellus_variant(hits)
    result.variants = len(grouped)
    result.collapsed_object_ids = sum(max(len(rows) - 1, 0) for rows in grouped.values())
    if not grouped:
        result.errors.append("Aucune tellus_variant dans l'export.")
        return result

    supplier = resolve_gedimat_supplier(session)
    result.supplier_id = supplier.id
    catalog = _ensure_catalog_row(session, row_count=len(grouped))
    existing = {
        sp.supplier_reference: sp
        for sp in session.scalars(
            select(SupplierProduct).where(SupplierProduct.supplier_id == supplier.id)
        ).all()
    }
    features = {
        row.supplier_product_id: row
        for row in session.scalars(
            select(SupplierProductFeature).where(
                SupplierProductFeature.supplier_product_id.in_(
                    [sp.id for sp in existing.values()]
                )
            )
        ).all()
    } if existing else {}

    seen: set[str] = set()
    for sku, rows in grouped.items():
        seen.add(sku)
        chosen = _prefer_hit(rows)
        proposed = _catalog_fields(chosen)
        meta = _feature_payload(sku, rows, chosen)
        sp = existing.get(sku)
        if sp is None:
            sp = SupplierProduct(
                product_id=None,
                supplier_id=supplier.id,
                supplier_reference=sku[:60],
                designation=proposed["designation"],
                supplier_unit=proposed["supplier_unit"],
                reference_quantity=proposed["reference_quantity"],
                packaging_quantity=proposed["packaging_quantity"],
                reference_unit=proposed["reference_unit"],
                brand=proposed["brand"],
                ean=proposed["ean"],
                image_url=proposed["image_url"],
                active=True,
                introduced_by_catalog_id=catalog.id,
                correction_source="import",
            )
            session.add(sp)
            session.flush()
            existing[sku] = sp
            _write_feature(session, sp.id, meta, current=None)
            result.created += 1
            continue
        feature = features.get(sp.id)
        changed = _apply_update(sp, proposed, catalog_id=catalog.id)
        feature_changed = _write_feature(session, sp.id, meta, current=feature)
        if sp.active is False:
            sp.active = True
            changed = True
        if changed or feature_changed:
            result.updated += 1
        else:
            result.unchanged += 1

    for sku, sp in existing.items():
        if sku in seen or not sp.active:
            continue
        sp.active = False
        result.deactivated += 1

    catalog.rows = len(grouped)
    catalog.supplier_references = len(grouped)
    catalog.rows_without_price = len(grouped)
    catalog.active = True
    catalog.status = "imported"
    session.flush()
    return result


def _prefer_hit(rows: list[dict]) -> dict:
    def rank(hit: dict) -> tuple:
        ean = _ean(hit.get("ean_tellus"))
        return (1 if ean else 0, len(str(hit.get("name") or "")))

    return max(rows, key=rank)


def _catalog_fields(hit: dict) -> dict[str, Any]:
    sku = str(hit.get("tellus_variant") or "").strip()
    name = str(hit.get("name") or "").strip() or sku
    supplier_unit, reference_unit = _units(hit.get("unite_vente"))
    quantity = _pack_quantity(hit.get("conditionnement"))
    return {
        "designation": name[:200],
        "supplier_unit": supplier_unit[:40],
        "reference_unit": reference_unit,
        "reference_quantity": quantity,
        "packaging_quantity": Decimal("1"),
        "brand": _brand(hit.get("brand")),
        "ean": _ean(hit.get("ean_tellus")),
        "image_url": _image(hit),
    }


def _apply_update(sp: SupplierProduct, proposed: dict[str, Any], *, catalog_id: int) -> bool:
    changed = False
    protected = sp.correction_source in CORRECTION_SOURCES_PROTECTED
    writable = {
        "designation": proposed["designation"],
        "brand": proposed["brand"],
        "ean": proposed["ean"],
        "image_url": proposed["image_url"],
        "active": True,
    }
    if not protected:
        writable["supplier_unit"] = proposed["supplier_unit"]
        writable["reference_unit"] = proposed["reference_unit"]
        writable["reference_quantity"] = proposed["reference_quantity"]
        writable["packaging_quantity"] = proposed["packaging_quantity"]
    for key, value in writable.items():
        if key in {"ean", "brand", "image_url"} and value is None:
            continue
        current = getattr(sp, key)
        if current != value:
            setattr(sp, key, value)
            changed = True
    if sp.introduced_by_catalog_id is None:
        sp.introduced_by_catalog_id = catalog_id
        changed = True
    return changed


def _feature_payload(sku: str, rows: list[dict], chosen: dict) -> dict:
    object_ids = sorted({str(row.get("objectID")) for row in rows if row.get("objectID")})
    return {
        "tellus_variant": sku,
        "tellus": _text(chosen.get("tellus")),
        "objectID": _text(chosen.get("objectID")),
        "object_ids": object_ids,
        "id": chosen.get("id"),
        "ean_tellus": _ean(chosen.get("ean_tellus")),
        "reference": _text(chosen.get("reference")),
        "code_article": _text(chosen.get("code_article")),
        "code_interne_erp": _text(chosen.get("code_interne_erp")),
        "name_variant": _text(chosen.get("name_variant")),
        "legend": _text(chosen.get("legend")),
        "list_categories": chosen.get("list_categories"),
        "hierarchical_categories": chosen.get("hierarchical_categories"),
        "id_famille": chosen.get("id_famille"),
        "famille_id": chosen.get("famille_id"),
        "properties": chosen.get("properties") if isinstance(chosen.get("properties"), dict) else {},
        "conditionnement": chosen.get("conditionnement"),
        "url": sanitize_http_url(_text(chosen.get("url")), max_length=500),
        "thumb": sanitize_http_url(_text(chosen.get("thumb")), max_length=500),
        "img": sanitize_http_url(_text(chosen.get("img")), max_length=500),
    }


def _write_feature(
    session: Session,
    supplier_product_id: int,
    payload: dict,
    *,
    current: SupplierProductFeature | None,
) -> bool:
    if current is None:
        session.add(
            SupplierProductFeature(
                supplier_product_id=supplier_product_id,
                category_code=FEATURE_CATEGORY,
                attributes=payload,
                extractor_version=FEATURE_VERSION,
                confidence=None,
            )
        )
        return True
    if current.attributes == payload and current.category_code == FEATURE_CATEGORY:
        return False
    current.category_code = FEATURE_CATEGORY
    current.attributes = payload
    current.extractor_version = FEATURE_VERSION
    return True


def _ensure_catalog_row(session: Session, *, row_count: int) -> SupplierImport:
    existing = session.scalar(
        select(SupplierImport).where(SupplierImport.source_key == CATALOG_SOURCE_KEY)
    )
    if existing is not None:
        return existing
    catalog = SupplierImport(
        source_key=CATALOG_SOURCE_KEY,
        filename="gedimat_store_catalog.json",
        supplier_name=SUPPLIER_NAME,
        status="imported",
        active=True,
        tax_basis=None,
        rows=row_count,
        rows_with_price=0,
        rows_without_price=row_count,
        agencies=0,
        supplier_references=row_count,
        offers=0,
        mapped=0,
        unmapped=row_count,
        error_count=0,
    )
    session.add(catalog)
    session.flush()
    return catalog


def _units(raw: Any) -> tuple[str, str]:
    code = ""
    label = ""
    if isinstance(raw, dict):
        code = str(raw.get("code") or "").strip().upper()
        label = str(raw.get("name") or "").strip()
    elif isinstance(raw, str):
        label = raw.strip()
    mapped = _UNIT_BY_CODE.get(code)
    if mapped is not None:
        supplier_unit, reference_unit = mapped
        return supplier_unit, reference_unit
    canonical = normalize_unit(label)
    if canonical in ALLOWED_PRODUCT_UNITS:
        return canonical, canonical
    return (label or "pièce")[:40], "pièce"


def _pack_quantity(raw: Any) -> Decimal:
    quantity = None
    if isinstance(raw, dict):
        quantity = raw.get("quantite")
    if quantity is None:
        return Decimal("1")
    try:
        number = Decimal(str(quantity))
    except Exception:
        return Decimal("1")
    if number <= 0:
        return Decimal("1")
    return number.quantize(Decimal("0.001"))


def _brand(raw: Any) -> str | None:
    if isinstance(raw, dict):
        raw = raw.get("name") or raw.get("label")
    return _text(raw, limit=80)


def _ean(raw: Any) -> str | None:
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(digits) not in {8, 12, 13, 14}:
        return None
    return digits


def _image(hit: dict) -> str | None:
    for key in ("img", "thumb"):
        url = sanitize_http_url(_text(hit.get(key)), max_length=500)
        if url:
            return url
    return None


def _text(raw: Any, *, limit: int = 200) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    return text[:limit]
