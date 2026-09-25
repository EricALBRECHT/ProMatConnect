"""Régression preview : catalogues nationaux TTC sans agence magasin."""

from __future__ import annotations

from app.connectors.file_csv import FileSupplierConnector
from app.services.supplier_import import SupplierImportService

NATIONAL_HEADERS = (
    "supplier,product_external_reference,product_name,brand,price,currency,tax_basis,"
    "supplier_unit,packaging_quantity,reference_unit,reference_quantity,source_url,"
    "verification_status,observed_at,agency_external_id,agency_name\n"
)

NATIONAL_ROWS = (
    "Leroy Merlin,LM-BA13-2500,Plaque BA13 Leroy,LM,12.90,EUR,TTC,plaque,1,plaque,1,"
    "https://example.test/lm-ba13,verified,2026-09-01T10:00:00+00:00,,National\n"
    "Brico Dépôt,BD-BA13-2500,Plaque BA13 Brico,BD,11.50,EUR,TTC,plaque,1,plaque,1,"
    ",,2026-09-01T10:00:00+00:00,,\n"
)


def test_national_ttc_preview_empty_agency_no_image(session):
    raw = (NATIONAL_HEADERS + NATIONAL_ROWS).encode("utf-8")
    connector, errors, warnings = FileSupplierConnector.parse_upload(
        raw, filename="promatconnect_import_test_prix_connus.csv"
    )
    assert errors == []
    assert len(connector.normalized_offers) == 2
    assert all(o.tax_basis == "TTC" for o in connector.normalized_offers)
    assert all((o.agency.external_id or "") == "" for o in connector.normalized_offers)
    assert all(o.agency.latitude is None and o.agency.longitude is None for o in connector.normalized_offers)
    assert all(o.product.image_url is None for o in connector.normalized_offers)
    assert all(o.price is not None for o in connector.normalized_offers)
    assert {o.supplier for o in connector.normalized_offers} == {"Leroy Merlin", "Brico Dépôt"}

    report = SupplierImportService(session).preview(
        raw, "promatconnect_import_test_prix_connus.csv"
    )
    body = report.to_dict()
    assert body["valid"] is True
    assert body["rows"] == 2
    assert body["rows_with_price"] == 2
    assert body["rows_without_price"] == 0
    assert body["rows_ttc"] == 2
    assert body["rows_ht"] == 0
    assert body["agencies"] == 0  # aucune agence magasin artificielle
    assert body["tax_basis"] == "TTC"
    assert "agency_external_id" in body["detected_columns"]
    assert "product_external_reference" in body["detected_columns"]
    assert len(body["sample_rows"]) == 2
    assert all(row.get("image_url") in (None, "") for row in body["sample_rows"])


def test_preview_api_national_ttc(client, session):
    raw = (NATIONAL_HEADERS + NATIONAL_ROWS).encode("utf-8")
    response = client.post(
        "/api/supplier-imports/preview",
        files={"file": ("promatconnect_import_test_prix_connus.csv", raw, "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["rows"] == 2
    assert body["rows_with_price"] == 2
    assert body["rows_without_price"] == 0
    assert body["rows_ttc"] == 2
    assert body["agencies"] == 0
    assert body["errors"] == []


def test_preview_ui_exposes_errors_not_zero_stats(client):
    """Erreur de parsing → valid false + errors (l'UI ne doit pas afficher de faux zéros)."""
    raw = b"supplier,agency_external_id,product_name,currency,tax_basis\nx,1,n,EUR,HT\n"
    response = client.post(
        "/api/supplier-imports/preview",
        files={"file": ("bad.csv", raw, "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["errors"]
    assert any(e["code"] == "missing_columns" for e in body["errors"])
    assert "detected_columns" in body


def test_supplier_reference_alias_accepted():
    header = (
        "supplier,agency_external_id,supplier_reference,product_name,"
        "price,currency,tax_basis\n"
    )
    raw = (header + "S,A1,REF-ALIAS,Name,1.5,EUR,HT\n").encode()
    connector, errors, _w = FileSupplierConnector.parse_upload(raw, filename="alias.csv")
    assert errors == []
    assert connector.normalized_offers[0].product.external_reference == "REF-ALIAS"
