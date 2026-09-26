"""Conversion HT/TTC — Decimal, vat_rate, comparaison mixte, snapshot."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.connectors.base import AgencyData, ConnectorOffer
from app.connectors.file_csv import FileSupplierConnector
from app.schemas.catalog import ProductRead
from app.schemas.comparison import CartLine
from app.services.comparison import ComparisonService, line_cost, required_packs, resolve_tax_basis
from app.services.tax import (
    CONVERSION_UNAVAILABLE_MSG,
    DEFAULT_COMPARE_TAX_BASIS,
    can_compare_in_basis,
    convert_amount,
    display_converted,
    money_round,
    parse_vat_rate,
    validate_vat_rate,
)
from app.version import APP_VERSION


class _Stub:
    def __init__(self, name, offers):
        self.name, self.offers = name, offers

    @property
    def supplier_name(self):
        return self.name

    def get_offers(self, product_ids):
        return [o for o in self.offers if o.product_id in product_ids]


def _agency(aid=10, geo=False):
    return AgencyData(
        id=aid,
        name="National" if not geo else "Magasin",
        address="",
        postal_code="",
        city="",
        latitude=48.85 if geo else None,
        longitude=2.35 if geo else None,
    )


def test_display_conversions_match_spec():
    assert display_converted(Decimal("25.90"), "TTC", Decimal("20"), "HT") == Decimal("21.58")
    assert display_converted(Decimal("8.37"), "TTC", Decimal("20"), "HT") == Decimal("6.98")
    assert display_converted(Decimal("34.27"), "TTC", Decimal("20"), "HT") == Decimal("28.56")
    assert display_converted(Decimal("21.58"), "HT", Decimal("20"), "TTC") == Decimal("25.90")


def test_unknown_vat_blocks_other_basis():
    assert display_converted(Decimal("25.90"), "TTC", None, "TTC") == Decimal("25.90")
    assert display_converted(Decimal("25.90"), "TTC", None, "HT") is None
    assert display_converted(Decimal("21.58"), "HT", None, "HT") == Decimal("21.58")
    assert display_converted(Decimal("21.58"), "HT", None, "TTC") is None
    assert can_compare_in_basis("TTC", None, "HT") is False
    assert can_compare_in_basis("HT", None, "TTC") is False
    assert can_compare_in_basis("TTC", Decimal("20"), "HT") is True


def test_vat_rate_csv_parse_and_validate():
    assert parse_vat_rate("20") == Decimal("20")
    assert parse_vat_rate("20.0") == Decimal("20.0")
    assert parse_vat_rate("20.00") == Decimal("20.00")
    assert parse_vat_rate("") is None
    assert parse_vat_rate(None) is None
    assert validate_vat_rate(Decimal("20"))
    assert not validate_vat_rate(Decimal("-1"))
    assert not validate_vat_rate(Decimal("101"))


def test_csv_vat_rate_valid_absent_invalid():
    header = (
        "supplier,agency_external_id,product_external_reference,product_name,"
        "price,currency,tax_basis,vat_rate,supplier_unit,packaging_quantity,"
        "reference_unit,reference_quantity\n"
    )
    ok = header + "LM,,R1,Rail,25.90,EUR,TTC,20,lot,10,pièce,10\n"
    connector, errors, _ = FileSupplierConnector.parse_upload(ok.encode(), filename="ok.csv")
    assert not errors
    assert connector.normalized_offers[0].vat_rate == Decimal("20")

    # without vat_rate column — still valid, vat_rate None
    c2, e2, _ = FileSupplierConnector.parse_upload(
        (
            "supplier,agency_external_id,product_external_reference,product_name,"
            "price,currency,tax_basis,supplier_unit,packaging_quantity,"
            "reference_unit,reference_quantity\n"
            "LM,,R1,Rail,25.90,EUR,TTC,lot,10,pièce,10\n"
        ).encode(),
        filename="novat.csv",
    )
    assert not e2
    assert c2.normalized_offers[0].vat_rate is None

    bad = header + "LM,,R1,Rail,25.90,EUR,TTC,abc,lot,10,pièce,10\n"
    _, e3, _ = FileSupplierConnector.parse_upload(bad.encode(), filename="bad.csv")
    assert any(e["code"] == "invalid_vat_rate" for e in e3)

    out = header + "LM,,R1,Rail,25.90,EUR,TTC,150,lot,10,pièce,10\n"
    _, e4, _ = FileSupplierConnector.parse_upload(out.encode(), filename="out.csv")
    assert any(e["code"] == "invalid_vat_rate" for e in e4)


def test_mixed_ht_ttc_comparable_with_vat():
    ht_offer = ConnectorOffer(
        supplier="A",
        product_id=1,
        price=Decimal("21.58"),
        stock=0,
        reference_quantity=Decimal("1"),
        supplier_reference="HT1",
        supplier_unit="plaque",
        reference_unit="pièce",
        packaging_quantity=Decimal("1"),
        preparation_minutes=30,
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        agency=_agency(1),
        tax_basis="HT",
        vat_rate=Decimal("20"),
    )
    ttc_offer = ConnectorOffer(
        supplier="B",
        product_id=1,
        price=Decimal("25.90"),
        stock=0,
        reference_quantity=Decimal("1"),
        supplier_reference="TTC1",
        supplier_unit="plaque",
        reference_unit="pièce",
        packaging_quantity=Decimal("1"),
        preparation_minutes=30,
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        agency=_agency(2),
        tax_basis="TTC",
        vat_rate=Decimal("20"),
    )
    products = {
        1: ProductRead(
            id=1, code="P1", name="Plaque", category="X", reference_unit="pièce", description=None
        )
    }
    service = ComparisonService(
        [_Stub("A", [ht_offer]), _Stub("B", [ttc_offer])],
        48.85,
        2.35,
        tax_basis="HT",
    )
    result = service.compare([CartLine(product_id=1, quantity=Decimal("1"))], products)
    assert result.tax_basis == "HT"
    assert result.excluded_incompatible_tax_basis == 0
    minimum = next(s for s in result.strategies if s.key == "minimum_materials")
    assert minimum.valid
    # Meilleur = 21.58 HT (source HT ou équivalent TTC)
    assert minimum.material_total == Decimal("21.58")


def test_lot_packs_unchanged_by_tax_conversion():
    offer = ConnectorOffer(
        supplier="LM",
        product_id=21,
        price=Decimal("25.90"),
        stock=0,
        reference_quantity=Decimal("10"),
        supplier_reference="85343284",
        supplier_unit="lot",
        reference_unit="pièce",
        packaging_quantity=Decimal("10"),
        preparation_minutes=60,
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        agency=_agency(10),
        tax_basis="TTC",
        vat_rate=Decimal("20"),
    )
    assert required_packs(Decimal("11"), offer.reference_quantity) == 2
    assert line_cost(offer, Decimal("11"), compare_basis="TTC") == Decimal("51.80")
    ht_total = line_cost(offer, Decimal("11"), compare_basis="HT")
    # 51.80 / 1.20 = 43.1666... → 43.17
    assert ht_total == money_round(Decimal("51.80") / Decimal("1.20"))
    assert ht_total == Decimal("43.17")
    # packs inchangés
    products = {
        21: ProductRead(
            id=21, code="R", name="Rail", category="O", reference_unit="pièce", description=None
        )
    }
    result = ComparisonService(
        [_Stub("LM", [offer])], 48.85, 2.35, tax_basis="HT"
    ).compare([CartLine(product_id=21, quantity=Decimal("11"))], products)
    line = next(s for s in result.strategies if s.key == "minimum_materials").lines[0]
    assert line.packs == 2
    assert line.purchased_quantity == Decimal("20")
    assert line.source_price == Decimal("25.90")
    assert line.source_tax_basis == "TTC"
    assert line.vat_rate == Decimal("20")
    assert line.tax_basis == "HT"
    assert line.line_total == Decimal("43.17")


def test_resolve_tax_basis_excludes_without_vat_for_other_basis():
    ttc_no_vat = ConnectorOffer(
        supplier="B",
        product_id=1,
        price=Decimal("10"),
        stock=100,
        reference_quantity=Decimal("1"),
        supplier_reference="X",
        supplier_unit="u",
        preparation_minutes=10,
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        agency=_agency(1, geo=True),
        tax_basis="TTC",
        vat_rate=None,
    )
    basis, kept, excluded, note = resolve_tax_basis([ttc_no_vat], "HT")
    assert basis == "HT"
    assert kept == []
    assert excluded == 1
    assert CONVERSION_UNAVAILABLE_MSG in (note or "")
    assert DEFAULT_COMPARE_TAX_BASIS == "HT"


def test_snapshot_preserves_source_tax_fields(client, session):
    from sqlalchemy import select

    from app.models import Product
    from scripts.normalized_catalog import seed_normalized_catalog

    seed_normalized_catalog(session)
    session.commit()
    plaque = session.scalar(select(Product).where(Product.code == "PMC-BA13-STD-2500X1200"))
    assert plaque
    chantier = client.post(
        "/api/chantiers",
        json={
            "nom": "Tax snapshot",
            "adresse": "10 rue du Chantier, 75004 Paris",
            "materiaux": [{"product_id": plaque.id, "quantite": "1", "ordre": 0}],
        },
    ).json()
    strategy = {
        "key": "minimum_materials",
        "title": "Prix matériaux minimum",
        "valid": True,
        "explanation": "test",
        "material_total": "6.98",
        "estimated_procurement_cost": "6.98",
        "stops": [
            {
                "id": 10,
                "supplier": "LM",
                "name": "National",
                "address": "",
                "postal_code": "",
                "city": "",
                "distance_km": None,
                "is_geolocated": False,
            }
        ],
        "supplier_count": 1,
        "lines": [
            {
                "product_id": plaque.id,
                "product_name": plaque.name,
                "reference_unit": "pièce",
                "requested_quantity": "1",
                "purchased_quantity": "1",
                "packs": 1,
                "supplier": "LM",
                "supplier_reference": "70505960",
                "supplier_unit": "plaque",
                "agency_id": 10,
                "pack_price": "6.98",
                "line_total": "6.98",
                "available_quantity": "1000000",
                "preparation_minutes": 60,
                "updated_at": "2026-01-15T00:00:00+00:00",
                "tax_basis": "HT",
                "packaging_quantity": "1",
                "reference_quantity": "1",
                "source_price": "8.37",
                "source_tax_basis": "TTC",
                "vat_rate": "20",
            }
        ],
        "unavailable": [],
    }
    put = client.put(
        f"/api/chantiers/{chantier['id']}/approvisionnement",
        json={
            "updated_at": chantier["updated_at"],
            "needs_fingerprint": f"{plaque.id}:1.000",
            "strategy": strategy,
            "origin": {
                "type": "site",
                "label": "Chantier",
                "address": chantier["adresse"],
                "latitude": 48.8566,
                "longitude": 2.3522,
                "source": "test",
            },
            "currency": "EUR",
            "tax_basis": "HT",
        },
    )
    assert put.status_code == 200, put.text
    appro = client.get(f"/api/chantiers/{chantier['id']}/approvisionnement").json()
    assert appro["tax_basis"] == "HT"
    line = appro["snapshot"]["strategy"]["lines"][0]
    assert line["source_price"] == "8.37"
    assert line["source_tax_basis"] == "TTC"
    assert line["vat_rate"] == "20"
    assert line["tax_basis"] == "HT"
    assert line["packs"] == 1

    # Ancien snapshot sans source_* toujours lisible
    old = {
        "product_id": plaque.id,
        "product_name": "Old",
        "reference_unit": "pièce",
        "requested_quantity": "1",
        "purchased_quantity": "1",
        "packs": 1,
        "supplier": "LM",
        "supplier_reference": "x",
        "supplier_unit": "plaque",
        "agency_id": 10,
        "pack_price": "8.37",
        "line_total": "8.37",
        "available_quantity": "1",
        "preparation_minutes": 10,
        "updated_at": "2026-01-01T00:00:00+00:00",
        "tax_basis": "TTC",
    }
    from app.schemas.comparison import SelectedLine

    parsed = SelectedLine.model_validate(old)
    assert parsed.source_price is None
    assert parsed.vat_rate is None

    liste = client.get(f"/api/chantiers/{chantier['id']}/liste-achat").json()
    assert liste["tax_basis"] == "HT"

    assert "tax-basis-toggle" in Path("app/templates/index.html").read_text(encoding="utf-8")
    assert "compareTaxBasis" in Path("app/static/app.js").read_text(encoding="utf-8")
    assert APP_VERSION.startswith("0.9.")
