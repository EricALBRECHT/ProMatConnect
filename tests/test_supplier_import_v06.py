"""v0.6.0 — connecteurs, normalisation, import CSV, preview, comparaison."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

from app.connectors.base import SupplierConnector
from app.connectors.demo import DemoSupplierConnector
from app.connectors.fake_pointp import FakePointPConnector
from app.connectors.file_csv import FileSupplierConnector
from app.connectors.normalized import NormalizedOffer
from app.connectors.registry import build_connectors
from app.models import Agency, Offer, Supplier, SupplierProduct
from app.repositories.offers import OfferRepository
from app.services.supplier_import import SupplierImportService
from app.version import APP_VERSION

EXAMPLE_CSV = Path(__file__).resolve().parents[1] / "examples" / "supplier_import_example.csv"

VALID_HEADER = (
    "supplier,agency_external_id,product_external_reference,product_name,"
    "price,currency,tax_basis\n"
)


def _csv(rows: str) -> bytes:
    return (VALID_HEADER + rows).encode("utf-8")


def test_app_version_060():
    assert APP_VERSION == "0.6.0"


def test_supplier_connector_interface(session):
    connector = FakePointPConnector(OfferRepository(session))
    assert isinstance(connector, SupplierConnector)
    assert connector.connector_key.startswith("demo:")
    assert connector.source_type == "demo"
    assert connector.display_name == "POINT.P TEST"
    health = connector.health()
    assert health.ok is True
    assert health.source_type == "demo"


def test_demo_connector_normalization_parity(session):
    demo = DemoSupplierConnector(OfferRepository(session), "POINT.P TEST")
    offers = demo.get_offers([1])
    assert offers
    assert all(isinstance(o.price, Decimal) for o in offers)
    assert all(o.product_id == 1 for o in offers)


def test_build_connectors_includes_demo_suppliers(session):
    connectors = build_connectors(session)
    names = {c.supplier_key for c in connectors}
    assert "POINT.P TEST" in names
    assert "GEDIMAT TEST" in names


def test_csv_valid_parse():
    data = EXAMPLE_CSV.read_bytes()
    connector, errors, warnings = FileSupplierConnector.parse_upload(data, filename="ok.csv")
    assert errors == []
    assert len(connector.normalized_offers) == 6
    assert all(isinstance(o, NormalizedOffer) for o in connector.normalized_offers)


def test_csv_missing_column():
    raw = b"supplier,agency_external_id,product_name,price,currency,tax_basis\nx,1,n,1,EUR,HT\n"
    _c, errors, _w = FileSupplierConnector.parse_upload(raw, filename="bad.csv")
    assert any(e["code"] == "missing_columns" for e in errors)


def test_csv_invalid_price():
    raw = _csv("S,A1,REF1,Name,abc,EUR,HT\n")
    _c, errors, _w = FileSupplierConnector.parse_upload(raw, filename="bad.csv")
    assert any(e["code"] == "invalid_price" for e in errors)


def test_csv_negative_price():
    raw = _csv("S,A1,REF1,Name,-1,EUR,HT\n")
    _c, errors, _w = FileSupplierConnector.parse_upload(raw, filename="bad.csv")
    assert any(e["code"] == "negative_price" for e in errors)


def test_csv_invalid_currency():
    raw = _csv("S,A1,REF1,Name,1,USD,HT\n")
    _c, errors, _w = FileSupplierConnector.parse_upload(raw, filename="bad.csv")
    assert any(e["code"] == "invalid_currency" for e in errors)


def test_csv_invalid_tax_basis():
    raw = _csv("S,A1,REF1,Name,1,EUR,TTC\n")
    _c, errors, _w = FileSupplierConnector.parse_upload(raw, filename="bad.csv")
    assert any(e["code"] == "invalid_tax_basis" for e in errors)


def test_csv_invalid_date():
    header = VALID_HEADER.strip() + ",observed_at\n"
    raw = (header + "S,A1,REF1,Name,1,EUR,HT,not-a-date\n").encode()
    _c, errors, _w = FileSupplierConnector.parse_upload(raw, filename="bad.csv")
    assert any(e["code"] == "invalid_date" for e in errors)


def test_csv_duplicate():
    raw = _csv("S,A1,REF1,Name,1,EUR,HT\nS,A1,REF1,Name,2,EUR,HT\n")
    _c, errors, _w = FileSupplierConnector.parse_upload(raw, filename="dup.csv")
    assert any(e["code"] == "duplicate" for e in errors)


def test_csv_empty_reference():
    raw = _csv("S,A1,,Name,1,EUR,HT\n")
    _c, errors, _w = FileSupplierConnector.parse_upload(raw, filename="bad.csv")
    assert any(e["code"] == "required" for e in errors)


def test_preview_no_db_write(session):
    before_offers = session.scalar(select(func.count()).select_from(Offer))
    before_agencies = session.scalar(select(func.count()).select_from(Agency))
    before_sp = session.scalar(select(func.count()).select_from(SupplierProduct))
    report = SupplierImportService(session).preview(EXAMPLE_CSV.read_bytes(), "supplier_import_example.csv")
    assert report.valid is True
    assert report.rows == 6
    assert report.agencies == 3
    assert report.supplier_references == 5
    assert report.offers == 6
    assert report.mapped >= 1
    assert report.unmapped >= 1
    assert report.errors == []
    assert session.scalar(select(func.count()).select_from(Offer)) == before_offers
    assert session.scalar(select(func.count()).select_from(Agency)) == before_agencies
    assert session.scalar(select(func.count()).select_from(SupplierProduct)) == before_sp


def test_preview_api_no_write(client, session):
    before = session.scalar(select(func.count()).select_from(Offer))
    response = client.post(
        "/api/supplier-imports/preview",
        files={"file": ("supplier_import_example.csv", EXAMPLE_CSV.read_bytes(), "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["mapped"] >= 1
    assert body["unmapped"] >= 1
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(Offer)) == before


def test_import_upsert_and_provenance(session):
    service = SupplierImportService(session)
    result = service.import_file(EXAMPLE_CSV.read_bytes(), "supplier_import_example.csv")
    assert result.valid is True
    assert result.source_key.startswith("import-")

    unmapped = session.scalar(
        select(SupplierProduct).where(SupplierProduct.supplier_reference == "PPT-FAKE-UNMAPPED")
    )
    assert unmapped is not None
    assert unmapped.product_id is None

    mapped = session.scalar(
        select(SupplierProduct).where(SupplierProduct.supplier_reference == "PPT-00137")
    )
    assert mapped is not None
    assert mapped.product_id == 1

    offer = session.scalar(
        select(Offer).where(
            Offer.supplier_product_id == mapped.id,
            Offer.source_type == "file",
        )
    )
    assert offer is not None
    assert offer.source_key == result.source_key
    assert offer.currency == "EUR"
    assert offer.tax_basis == "HT"

    agency = session.scalar(select(Agency).where(Agency.external_id == "PPT-AG-EST"))
    assert agency is not None

    # Upsert : second import met à jour sans doubler
    count_before = session.scalar(select(func.count()).select_from(Offer))
    service.import_file(EXAMPLE_CSV.read_bytes(), "supplier_import_example.csv")
    count_after = session.scalar(select(func.count()).select_from(Offer))
    assert count_after == count_before


def test_unmapped_excluded_from_comparison(session):
    SupplierImportService(session).import_file(
        EXAMPLE_CSV.read_bytes(), "supplier_import_example.csv"
    )
    offers = OfferRepository(session).for_supplier("POINT.P TEST", [1])
    refs = {o.supplier_reference for o in offers}
    assert "PPT-FAKE-UNMAPPED" not in refs
    assert "PPT-00137" in refs


def test_imported_offer_reaches_comparator(client, session):
    """Test central v0.6.0 : CSV → import → Offre → comparaison."""
    import_resp = client.post(
        "/api/supplier-imports",
        files={"file": ("supplier_import_example.csv", EXAMPLE_CSV.read_bytes(), "text/csv")},
    )
    assert import_resp.status_code == 200
    assert import_resp.json()["valid"] is True

    compare = client.post(
        "/api/compare",
        json={
            "lines": [{"product_id": 1, "quantity": 10}],
            "origin": {"type": "site", "latitude": 48.8566, "longitude": 2.3522},
        },
    )
    assert compare.status_code == 200
    payload = compare.json()
    strategies = payload.get("strategies") or payload.get("results") or []
    # Cherche le prix importé 8.50 dans les offres/stratégies
    blob = str(payload)
    assert "8.5" in blob or "8,50" in blob or "8.50" in blob

    # Preuve directe côté moteur via connecteur
    connectors = build_connectors(session)
    pointp = next(c for c in connectors if c.supplier_key == "POINT.P TEST")
    offers = pointp.get_offers([1])
    imported = [o for o in offers if o.price == Decimal("8.50")]
    assert imported, "L'offre importée à 8.50 doit atteindre le connecteur/comparateur"
    assert any(o.agency.name.startswith("POINT.P TEST · Import") for o in imported)


def test_ungeolocated_agency_excluded_from_comparator(session):
    """Agence sans coords : importée, mais hors comparateur (pas de fausse distance)."""
    header = (
        "supplier,agency_external_id,agency_name,product_external_reference,product_name,"
        "price,currency,tax_basis,product_code\n"
    )
    row = "POINT.P TEST,PPT-NO-GEO,Sans coords,PPT-00137,Plaque,7.77,EUR,HT,PMC0001\n"
    service = SupplierImportService(session)
    result = service.import_file((header + row).encode(), "nogeo.csv")
    assert result.valid is True
    agency = session.scalar(select(Agency).where(Agency.external_id == "PPT-NO-GEO"))
    assert agency is not None
    assert agency.latitude is None and agency.longitude is None
    offers = OfferRepository(session).for_supplier("POINT.P TEST", [1])
    assert all(o.price != Decimal("7.77") for o in offers)
    assert all(o.agency.name != "Sans coords" for o in offers)


def test_admin_page(client):
    response = client.get("/admin/fournisseurs")
    assert response.status_code == 200
    text = response.text
    assert "Fournisseurs" in text
    assert "Importer un CSV" in text
    assert "protéger avant" in text.lower() or "authentification" in text.lower()
    assert f"admin_suppliers.js?v={APP_VERSION}" in text


def test_health_version_and_sources(client):
    body = client.get("/api/health").json()
    assert body["version"] == "0.6.0"
    assert "demo" in body["data_sources"]
    assert "file" in body["data_sources"]
