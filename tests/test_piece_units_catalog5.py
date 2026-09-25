"""Corrections unités pièce catalogue 5 + conditionnement plaques/rails."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.connectors.base import AgencyData, ConnectorOffer
from app.connectors.file_csv import FileSupplierConnector
from app.models import Product
from app.repositories.offers import OfferRepository
from app.schemas.catalog import ProductRead
from app.schemas.comparison import CartLine
from app.services.comparison import ComparisonService, line_cost, required_packs
from scripts.correct_ossature_catalog5 import (
    CATALOG5_PIECE_FIXES,
    correct_catalog5_piece_units,
)
from scripts.normalized_catalog import seed_normalized_catalog
from scripts.seed import seed


def test_ba13_products_use_piece_not_m2(session):
    seed_normalized_catalog(session)
    session.commit()
    for code in [
        "PMC-BA13-STD-2500X1200",
        "PMC-BA13-STD-2500X600",
        "PMC-BA13-HYDRO-2500X1200",
        "PMC-BA13-MULTI-2500X1200",
        "PMC-BA13-LIGHT-2500X1200",
    ]:
        product = session.scalar(select(Product).where(Product.code == code))
        assert product is not None, code
        assert product.reference_unit == "pièce", code
        assert product.attributes and product.attributes.get("surface_m2"), code


def test_seed_corrects_plaque_unit_if_wrong(session):
    product = session.scalar(select(Product).where(Product.code == "PMC-BA13-HYDRO-2500X1200"))
    assert product is not None
    product.reference_unit = "m²"
    session.commit()
    seed(session)
    session.refresh(product)
    assert product.reference_unit == "pièce"
    assert product.attributes["surface_m2"] == 3.0


def test_csv_rejects_plaque_reference_unit_m2():
    raw = (
        "supplier,product_external_reference,product_name,brand,price,currency,tax_basis,"
        "supplier_unit,packaging_quantity,reference_unit,reference_quantity,"
        "agency_external_id,agency_name\n"
        "LEROY_MERLIN,70505960,Plaque de plâtre BA13 standard,LM,8.37,EUR,TTC,"
        "plaque,1,m2,3,,\n"
        "LEROY_MERLIN,70505961,Plaque de plâtre BA13 standard OK,LM,8.37,EUR,TTC,"
        "plaque,1,pièce,1,,\n"
    ).encode()
    connector, errors, _ = FileSupplierConnector.parse_upload(raw, filename="plq.csv")
    assert any(e["code"] == "plaque_unit_mismatch" for e in errors)
    assert len(connector.normalized_offers) == 1
    assert connector.normalized_offers[0].product.reference_unit == "pièce"


def test_plaque_lot_packaging_math():
    offer = ConnectorOffer(
        supplier="TEST",
        product_id=1,
        price=Decimal("40.00"),
        stock=100,
        reference_quantity=Decimal("5"),
        supplier_reference="LOT5",
        supplier_unit="lot",
        reference_unit="pièce",
        packaging_quantity=Decimal("5"),
        preparation_minutes=30,
        updated_at=__import__("datetime").datetime(2026, 1, 15, tzinfo=__import__("datetime").timezone.utc),
        agency=AgencyData(
            id=1,
            name="National",
            address="",
            postal_code="",
            city="",
            latitude=None,
            longitude=None,
        ),
        tax_basis="TTC",
    )
    assert required_packs(Decimal("1"), offer.reference_quantity) == 1
    assert line_cost(offer, Decimal("1")) == Decimal("40.00")
    assert required_packs(Decimal("6"), offer.reference_quantity) == 2
    assert line_cost(offer, Decimal("6")) == Decimal("80.00")


def test_plaque_unit_price_ten_pieces():
    offer = ConnectorOffer(
        supplier="LM",
        product_id=1,
        price=Decimal("8.37"),
        stock=0,  # national sans stock
        reference_quantity=Decimal("1"),
        supplier_reference="70505960",
        supplier_unit="plaque",
        reference_unit="pièce",
        packaging_quantity=Decimal("1"),
        preparation_minutes=60,
        updated_at=__import__("datetime").datetime(2026, 1, 15, tzinfo=__import__("datetime").timezone.utc),
        agency=AgencyData(
            id=99,
            name="LM national",
            address="",
            postal_code="",
            city="",
            latitude=None,
            longitude=None,
        ),
        tax_basis="TTC",
    )
    assert offer.covers_packs(10)
    assert line_cost(offer, Decimal("10")) == Decimal("83.70")


class _Stub:
    def __init__(self, name, offers):
        self.name, self.offers = name, offers

    @property
    def supplier_name(self):
        return self.name

    def get_offers(self, product_ids):
        return [o for o in self.offers if o.product_id in product_ids]


def test_national_catalog_price_without_geolocation():
    """Offre nationale sans lat/lon : prix matériaux OK, pas de distance fictive."""
    offer = ConnectorOffer(
        supplier="LEROY_MERLIN",
        product_id=21,
        price=Decimal("25.90"),
        stock=0,
        reference_quantity=Decimal("10"),
        supplier_reference="85343284",
        supplier_unit="lot",
        reference_unit="pièce",
        packaging_quantity=Decimal("10"),
        preparation_minutes=60,
        updated_at=__import__("datetime").datetime(2026, 1, 15, tzinfo=__import__("datetime").timezone.utc),
        agency=AgencyData(
            id=10,
            name="LEROY_MERLIN (catalogue national)",
            address="",
            postal_code="",
            city="",
            latitude=None,
            longitude=None,
        ),
        tax_basis="TTC",
    )
    products = {
        21: ProductRead(
            id=21,
            code="PMC-RAIL-R48-3000",
            name="Rail R48 3 m",
            category="Ossature",
            reference_unit="pièce",
            description=None,
        )
    }
    service = ComparisonService(
        [_Stub("LEROY_MERLIN", [offer])],
        48.8566,
        2.3522,
        tax_basis="TTC",
    )
    for qty, packs, purchased, total in [
        ("1", 1, Decimal("10"), Decimal("25.90")),
        ("10", 1, Decimal("10"), Decimal("25.90")),
        ("11", 2, Decimal("20"), Decimal("51.80")),
    ]:
        result = service.compare([CartLine(product_id=21, quantity=Decimal(qty))], products)
        assert result.tax_basis == "TTC"
        minimum = next(s for s in result.strategies if s.key == "minimum_materials")
        assert minimum.valid is True
        assert minimum.lines[0].packs == packs
        assert minimum.lines[0].purchased_quantity == purchased
        assert minimum.material_total == total
        assert minimum.lines[0].agency_id == 10
        assert minimum.total_distance_km is None
        assert minimum.route is None
        # Stratégies trajet indisponibles sans magasin géoloc
        single = next(s for s in result.strategies if s.key == "single_stop")
        assert single.valid is False
        assert "géolocalisée" in single.explanation.lower() or "national" in single.explanation.lower()


def test_correction_script_idempotent_on_fixture_rows(session):
    """Le script ne casse pas si les refs catalog5 sont absentes (anomalies listées)."""
    seed_normalized_catalog(session)
    session.commit()
    report = correct_catalog5_piece_units(session)
    # Sur DB de test seed, les refs LM/BD catalog5 n'existent pas → anomalies, 0 corrected.
    assert report.to_dict()["counts"]["corrected"] == 0
    assert len(report.anomalies) == len(CATALOG5_PIECE_FIXES)
    again = correct_catalog5_piece_units(session)
    assert again.to_dict()["counts"]["corrected"] == 0


def test_ungeolocated_agency_included_for_material_price(session):
    """Agence sans coords : offerte pour le prix, sans inventer de distance."""
    from app.services.supplier_import import SupplierImportService

    header = (
        "supplier,agency_external_id,agency_name,product_external_reference,product_name,"
        "price,currency,tax_basis,product_code,supplier_unit,packaging_quantity,"
        "reference_unit,reference_quantity\n"
    )
    row = (
        "POINT.P TEST,PPT-NO-GEO,Sans coords,PPT-00137,Plaque BA13,"
        "7.77,EUR,HT,PMC0001,plaque,1,plaque,1\n"
    )
    service = SupplierImportService(session)
    result = service.import_file((header + row).encode(), "nogeo.csv")
    assert result.valid is True
    offers = OfferRepository(session).for_supplier("POINT.P TEST", [1])
    imported = [o for o in offers if o.price == Decimal("7.77")]
    assert imported
    assert imported[0].agency.latitude is None
    assert imported[0].agency.longitude is None
