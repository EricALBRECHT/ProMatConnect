"""Rattachement des SupplierProduct sans équivalence normalisable.

Identité = désignation normalisée (casse, accents, espaces). Deux textes
identiques partagent un Product. Toute différence garde deux Product.
La référence fournisseur ne sert de clé que si la désignation est vide.
Elle n'est pas recopiée dans les attributs métier.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product, Supplier, SupplierProduct
from app.models.product_mapping import CORRECTION_SOURCE_SPECIFIC
from app.services.product_mapping.primitives.textutil import fold
from app.services.units import ALLOWED_PRODUCT_UNITS, normalize_unit

_SPACE = re.compile(r"\s+")
_PREFIX = "PMC-SPEC-"


def designation_key(designation: str | None) -> str:
    return _SPACE.sub(" ", fold(designation or "")).strip()


def specific_code(key: str) -> str:
    digest = hashlib.sha1(key.encode()).hexdigest()[:16].upper()
    return f"{_PREFIX}{digest}"


def _unit(values: list[str | None]) -> str:
    for raw in values:
        canonical = normalize_unit(raw)
        if canonical in ALLOWED_PRODUCT_UNITS:
            return canonical
    return "pièce"


def _category(key: str) -> str:
    token = key.split(" ", 1)[0] if key else "catalogue"
    label = token[:1].upper() + token[1:]
    return (label or "Catalogue")[:80]


def attach_specific(session: Session, *, supplier_name: str = "BRICO_DEPOT") -> dict[str, int]:
    """Crée les PMC spécifiques manquants et rattache les SP encore libres.

    N'écrit jamais un SupplierProduct qui a déjà un product_id.
    """
    supplier_id = session.scalar(select(Supplier.id).where(Supplier.name == supplier_name))
    rows = session.scalars(
        select(SupplierProduct).where(
            SupplierProduct.supplier_id == supplier_id,
            SupplierProduct.product_id.is_(None),
        )
    ).all()
    groups: dict[str, list[SupplierProduct]] = defaultdict(list)
    for sp in rows:
        key = designation_key(sp.designation)
        if not key:
            key = "sku:" + (sp.supplier_reference or str(sp.id))
        groups[key].append(sp)

    existing = {
        product.code: product
        for product in session.scalars(select(Product).where(Product.code.like(f"{_PREFIX}%"))).all()
    }
    created = 0
    mapped = 0
    for key, members in groups.items():
        code = specific_code(key)
        product = existing.get(code)
        if product is not None:
            stored = (product.attributes or {}).get("designation_key")
            if stored not in (None, key):
                code = f"{_PREFIX}{hashlib.sha1(key.encode()).hexdigest()[:20].upper()}"
                product = existing.get(code)
        if product is None:
            label = max((sp.designation or "" for sp in members), key=len).strip() or "Produit spécifique"
            uses_sku = key.startswith("sku:")
            product = Product(
                code=code,
                name=label[:200],
                category=_category("" if uses_sku else key),
                subcategory="Spécifique",
                reference_unit=_unit([sp.reference_unit or sp.supplier_unit for sp in members]),
                description=None,
                attributes={
                    "identity_level": "specific",
                    "designation_key": None if uses_sku else key,
                },
                is_active=True,
                is_legacy=False,
            )
            session.add(product)
            session.flush()
            existing[code] = product
            created += 1
        for sp in members:
            if sp.product_id is not None:
                continue
            sp.product_id = product.id
            sp.correction_source = CORRECTION_SOURCE_SPECIFIC
            mapped += 1
    if created or mapped:
        session.flush()
    return {"created": created, "mapped": mapped, "groups": len(groups)}
