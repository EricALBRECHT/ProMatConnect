"""Conditionnement rails/montants/fourrures : besoin en pièces, pas en mètres."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.connectors.base import AgencyData, ConnectorOffer
from app.models import Product
from app.services.comparison import line_cost, required_packs
from scripts.normalized_catalog import seed_normalized_catalog
from scripts.seed import seed


def _rail_offer(*, pack: str, price: str = "12.00", stock: int = 100) -> ConnectorOffer:
    """pack = nb de pièces PMC dans un conditionnement fournisseur (= reference_quantity)."""
    pack_qty = Decimal(pack)
    return ConnectorOffer(
        supplier="TEST",
        product_id=1,
        price=Decimal(price),
        stock=stock,
        reference_quantity=pack_qty,
        supplier_reference="RAIL-LOT",
        supplier_unit="lot" if pack_qty > 1 else "pièce",
        reference_unit="pièce",
        packaging_quantity=pack_qty,
        preparation_minutes=30,
        updated_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        agency=AgencyData(
            id=1,
            name="Agence test",
            address="1 rue Test",
            postal_code="75012",
            city="Paris",
            latitude=48.84,
            longitude=2.35,
        ),
    )


def test_ossature_products_use_piece_not_meter(session):
    seed_normalized_catalog(session)
    session.commit()
    codes = [
        "PMC-RAIL-R48-3000",
        "PMC-RAIL-R70-3000",
        "PMC-MONTANT-M48-2500",
        "PMC-MONTANT-M48-3000",
        "PMC-FOURRURE-F45-3000",
    ]
    for code in codes:
        product = session.scalar(select(Product).where(Product.code == code))
        assert product is not None, code
        assert product.reference_unit == "pièce", code
        assert product.attributes and product.attributes.get("length_mm"), code


def test_legacy_rail_demo_untouched_as_piece(session):
    """PMC0002 démo reste inchangé (déjà en pièce) — pas de recyclage destructif."""
    demo = session.scalar(select(Product).where(Product.code == "PMC0002"))
    assert demo is not None
    assert demo.reference_unit == "pièce"
    assert demo.is_legacy is True


def test_rail_lot_of_10_need_1():
    """1 rail, lot de 10 → 1 lot / 10 pièces achetées."""
    offer = _rail_offer(pack="10", price="25.00")
    packs = required_packs(Decimal("1"), offer.reference_quantity)
    assert packs == 1
    assert packs * offer.reference_quantity == Decimal("10")
    assert line_cost(offer, Decimal("1")) == Decimal("25.00")


def test_rail_lot_of_10_need_10():
    """10 rails, lot de 10 → 1 lot / 10 pièces."""
    offer = _rail_offer(pack="10", price="25.00")
    packs = required_packs(Decimal("10"), offer.reference_quantity)
    assert packs == 1
    assert packs * offer.reference_quantity == Decimal("10")
    assert line_cost(offer, Decimal("10")) == Decimal("25.00")


def test_rail_lot_of_10_need_11():
    """11 rails, lot de 10 → 2 lots / 20 pièces."""
    offer = _rail_offer(pack="10", price="25.00")
    packs = required_packs(Decimal("11"), offer.reference_quantity)
    assert packs == 2
    assert packs * offer.reference_quantity == Decimal("20")
    assert line_cost(offer, Decimal("11")) == Decimal("50.00")


def test_rail_sold_individually_exact_quantity():
    """Rail vendu à l'unité → quantité exacte."""
    offer = _rail_offer(pack="1", price="3.20")
    for qty in ("1", "12", "20"):
        packs = required_packs(Decimal(qty), offer.reference_quantity)
        assert packs == int(qty)
        assert packs * offer.reference_quantity == Decimal(qty)
        assert line_cost(offer, Decimal(qty)) == (Decimal("3.20") * packs).quantize(
            Decimal("0.01")
        )


def test_seed_corrects_ossature_unit_if_wrong(session):
    """Ré-exécution du seed corrige une unité 'm' erronée sur Product normalisé."""
    product = session.scalar(select(Product).where(Product.code == "PMC-RAIL-R48-3000"))
    assert product is not None
    product.reference_unit = "m"
    session.commit()
    seed(session)
    session.refresh(product)
    assert product.reference_unit == "pièce"
    assert product.attributes["length_mm"] == 3000


def test_csv_rejects_ossature_reference_unit_meter():
    """CSV ossature avec reference_unit=m : ligne refusée (non importable telle quelle)."""
    from app.connectors.file_csv import FileSupplierConnector

    raw = (
        "supplier,product_external_reference,product_name,brand,price,currency,tax_basis,"
        "supplier_unit,packaging_quantity,reference_unit,reference_quantity,"
        "agency_external_id,agency_name\n"
        "LEROY_MERLIN,85343284,Lot de 10 rails de 48 en 3 m NF,SEMIN,25.00,EUR,TTC,"
        "lot,1,m,30,,\n"
        "LEROY_MERLIN,67532150,Rail de 70 mm long. 3 m,,3.20,EUR,TTC,"
        "piece,1,pièce,1,,\n"
    ).encode()
    connector, errors, _warnings = FileSupplierConnector.parse_upload(raw, filename="oss.csv")
    assert any(e["code"] == "ossature_unit_mismatch" for e in errors)
    # Seule la ligne correctement en pièce est acceptée.
    assert len(connector.normalized_offers) == 1
    assert connector.normalized_offers[0].product.external_reference == "67532150"
    assert connector.normalized_offers[0].product.reference_unit == "pièce"


def test_csv_accepts_ossature_lot_of_10_as_pieces():
    from app.connectors.file_csv import FileSupplierConnector

    raw = (
        "supplier,product_external_reference,product_name,brand,price,currency,tax_basis,"
        "supplier_unit,packaging_quantity,reference_unit,reference_quantity,"
        "agency_external_id,agency_name\n"
        "LEROY_MERLIN,85343284,Lot de 10 rails de 48 en 3 m NF,SEMIN,25.00,EUR,TTC,"
        "lot,10,pièce,10,,\n"
    ).encode()
    connector, errors, _warnings = FileSupplierConnector.parse_upload(raw, filename="oss_ok.csv")
    assert errors == []
    offer = connector.normalized_offers[0]
    assert offer.product.supplier_unit == "lot"
    assert offer.product.packaging_quantity == Decimal("10")
    assert offer.product.reference_unit == "pièce"
    assert offer.product.reference_quantity == Decimal("10")
