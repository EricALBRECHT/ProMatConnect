"""v0.7 — image_url produit, catalogues, HT/TTC, prix optionnel."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

from app.connectors.file_csv import FileSupplierConnector
from app.connectors.url_safety import sanitize_http_url
from app.models import Offer, SupplierImport, SupplierProduct
from app.repositories.offers import OfferRepository
from app.schemas.comparison import SelectedLine
from app.services.comparison import resolve_tax_basis
from app.services.supplier_import import SupplierImportService
from app.connectors.base import AgencyData, ConnectorOffer
from datetime import datetime, timezone

EXAMPLE_CSV = Path(__file__).resolve().parents[1] / "examples" / "supplier_import_example.csv"

MIN_HEADER = (
    "supplier,agency_external_id,product_external_reference,product_name,"
    "price,currency,tax_basis\n"
)


def _csv(header: str, rows: str) -> bytes:
    return (header + rows).encode("utf-8")


def test_sanitize_http_url_accepts_https():
    assert sanitize_http_url("https://cdn.example.test/p.png") == "https://cdn.example.test/p.png"
    assert sanitize_http_url("http://cdn.example.test/p.png") == "http://cdn.example.test/p.png"


def test_sanitize_http_url_rejects_dangerous_schemes():
    assert sanitize_http_url("javascript:alert(1)") is None
    assert sanitize_http_url("data:image/png;base64,abc") is None
    assert sanitize_http_url("file:///etc/passwd") is None
    assert sanitize_http_url("ftp://example.test/a.png") is None
    assert sanitize_http_url("") is None
    assert sanitize_http_url(None) is None


def test_import_valid_image_url(session):
    header = (
        "supplier,agency_external_id,agency_name,agency_address,agency_postal_code,"
        "agency_city,agency_latitude,agency_longitude,product_external_reference,"
        "product_name,supplier_unit,packaging_quantity,price,currency,tax_basis,"
        "available_quantity,preparation_minutes,product_code,image_url\n"
    )
    rows = (
        "POINT.P TEST,PPT-IMG,POINT.P Img,1 rue X,75012,Paris,48.84,2.41,"
        "PPT-IMG-1,Produit image,plaque,1,8.50,EUR,HT,20,30,PMC0001,"
        "https://cdn.example.test/ba13.png\n"
    )
    result = SupplierImportService(session).import_file(
        _csv(header, rows), "with_image.csv"
    )
    assert result.valid is True
    sp = session.scalar(
        select(SupplierProduct).where(SupplierProduct.supplier_reference == "PPT-IMG-1")
    )
    assert sp is not None
    assert sp.image_url == "https://cdn.example.test/ba13.png"
    assert sp.product_id == 1


def test_import_absent_image_url(session):
    header = (
        "supplier,agency_external_id,agency_name,agency_address,agency_postal_code,"
        "agency_city,agency_latitude,agency_longitude,product_external_reference,"
        "product_name,supplier_unit,packaging_quantity,price,currency,tax_basis,"
        "available_quantity,preparation_minutes,product_code\n"
    )
    rows = (
        "POINT.P TEST,PPT-NOIMG,POINT.P NoImg,1 rue X,75012,Paris,48.84,2.41,"
        "PPT-NOIMG-1,Produit sans image,plaque,1,8.50,EUR,HT,20,30,PMC0001\n"
    )
    result = SupplierImportService(session).import_file(
        _csv(header, rows), "no_image.csv"
    )
    assert result.valid is True
    sp = session.scalar(
        select(SupplierProduct).where(SupplierProduct.supplier_reference == "PPT-NOIMG-1")
    )
    assert sp is not None
    assert sp.image_url is None


def test_import_invalid_image_url_neutralized(session):
    header = (
        "supplier,agency_external_id,agency_name,agency_address,agency_postal_code,"
        "agency_city,agency_latitude,agency_longitude,product_external_reference,"
        "product_name,supplier_unit,packaging_quantity,price,currency,tax_basis,"
        "available_quantity,preparation_minutes,product_code,image_url\n"
    )
    rows = (
        "POINT.P TEST,PPT-BADIMG,POINT.P Bad,1 rue X,75012,Paris,48.84,2.41,"
        "PPT-BADIMG-1,Produit bad image,plaque,1,8.50,EUR,HT,20,30,PMC0001,"
        "javascript:alert(1)\n"
    )
    connector, errors, warnings = FileSupplierConnector.parse_upload(
        _csv(header, rows), filename="bad_image.csv"
    )
    assert errors == []
    assert any(w["code"] == "invalid_image_url" for w in warnings)
    assert connector.normalized_offers[0].product.image_url is None

    result = SupplierImportService(session).import_file(
        _csv(header, rows), "bad_image.csv"
    )
    assert result.valid is True
    sp = session.scalar(
        select(SupplierProduct).where(SupplierProduct.supplier_reference == "PPT-BADIMG-1")
    )
    assert sp is not None
    assert sp.image_url is None


def test_image_url_preserved_on_supplier_product(session):
    SupplierImportService(session).import_file(EXAMPLE_CSV.read_bytes(), "supplier_import_example.csv")
    sp = session.scalar(
        select(SupplierProduct).where(SupplierProduct.supplier_reference == "PPT-00137")
    )
    assert sp.image_url == "https://cdn.example.test/ba13.png"
    rail = session.scalar(
        select(SupplierProduct).where(SupplierProduct.supplier_reference == "PPT-00959")
    )
    assert rail.image_url is None


def test_image_present_in_comparison_result(session):
    SupplierImportService(session).import_file(EXAMPLE_CSV.read_bytes(), "supplier_import_example.csv")
    offers = OfferRepository(session).for_supplier("POINT.P TEST", [1])
    with_image = [o for o in offers if o.supplier_reference == "PPT-00137"]
    assert with_image
    assert with_image[0].image_url == "https://cdn.example.test/ba13.png"


def test_absence_of_image_no_regression(session):
    """Offres démo sans image_url restent comparables."""
    offers = OfferRepository(session).for_supplier("POINT.P TEST", [1])
    assert offers
    assert all(o.image_url is None or isinstance(o.image_url, str) for o in offers)


def test_legacy_csv_without_image_url_column_compatible():
    raw = (MIN_HEADER + "POINT.P TEST,A1,REF-LEGACY,Name,1,EUR,HT\n").encode()
    connector, errors, warnings = FileSupplierConnector.parse_upload(
        raw, filename="legacy.csv"
    )
    assert errors == []
    assert connector.normalized_offers[0].product.image_url is None


def test_empty_price_no_offer(session):
    header = (
        "supplier,agency_external_id,agency_name,agency_address,agency_postal_code,"
        "agency_city,agency_latitude,agency_longitude,product_external_reference,"
        "product_name,supplier_unit,packaging_quantity,price,currency,tax_basis,"
        "available_quantity,preparation_minutes,product_code\n"
    )
    rows = (
        "POINT.P TEST,PPT-NOPRICE,POINT.P NoPrice,1 rue X,75012,Paris,48.84,2.41,"
        "PPT-NOPRICE-1,Sans prix,plaque,1,,EUR,HT,20,30,PMC0001\n"
    )
    preview = SupplierImportService(session).preview(_csv(header, rows), "noprice.csv")
    assert preview.valid is True
    assert preview.rows_without_price == 1
    assert preview.rows_with_price == 0

    result = SupplierImportService(session).import_file(_csv(header, rows), "noprice.csv")
    assert result.valid is True
    sp = session.scalar(
        select(SupplierProduct).where(SupplierProduct.supplier_reference == "PPT-NOPRICE-1")
    )
    assert sp is not None
    offer_count = session.scalar(
        select(func.count()).select_from(Offer).where(Offer.supplier_product_id == sp.id)
    )
    assert int(offer_count or 0) == 0


def test_preview_includes_sample_image_and_tax_stats(session):
    report = SupplierImportService(session).preview(
        EXAMPLE_CSV.read_bytes(), "supplier_import_example.csv"
    )
    body = report.to_dict()
    assert body["rows_ht"] == 6
    assert body["tax_basis"] == "HT"
    assert body["sample_rows"]
    assert any(r.get("image_url") for r in body["sample_rows"])
    assert any(r.get("image_url") is None for r in body["sample_rows"])


def test_catalog_deactivate_excludes_offers(session):
    """Les offres liées au catalogue désactivé disparaissent du comparateur."""
    header = (
        "supplier,agency_external_id,agency_name,agency_address,agency_postal_code,"
        "agency_city,agency_latitude,agency_longitude,product_external_reference,"
        "product_name,supplier_unit,packaging_quantity,price,currency,tax_basis,"
        "available_quantity,preparation_minutes,product_code\n"
    )
    rows = (
        "POINT.P TEST,PPT-CAT-ONLY,POINT.P Catalog Only,1 rue X,75012,Paris,48.84,2.41,"
        "PPT-CAT-ONLY-1,Produit catalogue seul,plaque,1,8.50,EUR,HT,20,30,PMC0001\n"
    )
    service = SupplierImportService(session)
    result = service.import_file(_csv(header, rows), "catalog_only.csv")
    catalog_id = result.catalog_id
    assert catalog_id is not None

    before = OfferRepository(session).for_supplier("POINT.P TEST", [1])
    assert "PPT-CAT-ONLY-1" in {o.supplier_reference for o in before}

    service.set_catalog_active(catalog_id, False)
    after = OfferRepository(session).for_supplier("POINT.P TEST", [1])
    assert "PPT-CAT-ONLY-1" not in {o.supplier_reference for o in after}
    # Offres démo (catalog_id NULL) restent.
    assert after

    service.set_catalog_active(catalog_id, True)
    restored = OfferRepository(session).for_supplier("POINT.P TEST", [1])
    assert "PPT-CAT-ONLY-1" in {o.supplier_reference for o in restored}


def test_catalog_delete_safe(session):
    service = SupplierImportService(session)
    result = service.import_file(EXAMPLE_CSV.read_bytes(), "supplier_import_example.csv")
    catalog_id = result.catalog_id
    catalogs = service.list_catalogs()
    entry = next(c for c in catalogs if c["id"] == catalog_id)
    assert entry["can_delete"] is True

    deleted = service.delete_catalog(catalog_id, confirm=True)
    assert deleted["deleted"] is True
    assert session.get(SupplierImport, catalog_id) is None
    remaining = session.scalar(
        select(func.count()).select_from(Offer).where(Offer.catalog_id == catalog_id)
    )
    assert int(remaining or 0) == 0


def test_catalog_delete_requires_confirm(session):
    service = SupplierImportService(session)
    result = service.import_file(EXAMPLE_CSV.read_bytes(), "supplier_import_example.csv")
    try:
        service.delete_catalog(result.catalog_id, confirm=False)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_resolve_tax_basis_prefers_ht_when_mixed():
    now = datetime.now(timezone.utc)
    agency = AgencyData(
        id=1, name="A", address="x", postal_code="75012", city="Paris",
        latitude=48.8, longitude=2.3,
    )

    def offer(basis: str, price: str = "10") -> ConnectorOffer:
        return ConnectorOffer(
            supplier="S",
            agency=agency,
            product_id=1,
            supplier_reference="R",
            supplier_unit="u",
            reference_quantity=Decimal("1"),
            price=Decimal(price),
            tax_basis=basis,
            stock=10,
            preparation_minutes=30,
            updated_at=now,
        )

    basis, kept, excluded, note = resolve_tax_basis(
        [offer("HT"), offer("TTC")], None
    )
    assert basis == "HT"
    assert len(kept) == 1
    assert excluded == 1
    assert note is not None


def test_selected_line_carries_image_url_for_snapshot():
    line = SelectedLine(
        product_id=1,
        product_name="Plaque",
        reference_unit="plaque",
        requested_quantity=Decimal("2"),
        purchased_quantity=Decimal("2"),
        packs=2,
        supplier="POINT.P TEST",
        supplier_reference="PPT-00137",
        supplier_unit="plaque",
        agency_id=1,
        pack_price=Decimal("8.50"),
        line_total=Decimal("17.00"),
        available_quantity=Decimal("80"),
        preparation_minutes=45,
        updated_at=datetime.now(timezone.utc),
        tax_basis="HT",
        image_url="https://cdn.example.test/ba13.png",
    )
    dumped = line.model_dump()
    assert dumped["image_url"] == "https://cdn.example.test/ba13.png"
    # Snapshot historique indépendant du catalogue courant.
    assert "image_url" in dumped


def test_catalog_api_activate_deactivate_delete(client, session):
    result = SupplierImportService(session).import_file(
        EXAMPLE_CSV.read_bytes(), "supplier_import_example.csv"
    )
    catalog_id = result.catalog_id

    res = client.post(f"/api/supplier-catalogs/{catalog_id}/deactivate")
    assert res.status_code == 200
    assert res.json()["active"] is False

    res = client.post(f"/api/supplier-catalogs/{catalog_id}/activate")
    assert res.status_code == 200
    assert res.json()["active"] is True

    res = client.delete(f"/api/supplier-catalogs/{catalog_id}")
    assert res.status_code == 400

    res = client.delete(f"/api/supplier-catalogs/{catalog_id}?confirm=true")
    assert res.status_code == 200
    assert res.json()["deleted"] is True
