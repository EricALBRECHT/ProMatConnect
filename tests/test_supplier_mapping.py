"""Mapping explicite SupplierProduct → Product (preview + catalogue)."""

from __future__ import annotations

from sqlalchemy import select

from app.connectors.file_csv import FileSupplierConnector
from app.models import Product, SupplierProduct
from app.repositories.offers import OfferRepository
from app.services.supplier_import import SupplierImportService

HEADER = (
    "supplier,agency_external_id,agency_name,agency_address,agency_postal_code,"
    "agency_city,agency_latitude,agency_longitude,product_external_reference,"
    "product_name,brand,supplier_unit,packaging_quantity,reference_unit,"
    "reference_quantity,price,currency,tax_basis,available_quantity,"
    "preparation_minutes\n"
)


def _csv(*rows: str) -> bytes:
    return (HEADER + "".join(rows)).encode("utf-8")


def test_preview_national_info_not_per_line_warning(session):
    raw = (
        "supplier,product_external_reference,product_name,brand,price,currency,tax_basis,"
        "supplier_unit,packaging_quantity,reference_unit,reference_quantity,source_url,"
        "verification_status,observed_at,agency_external_id,agency_name\n"
        "Leroy Merlin,LM-1,P1,LM,10,EUR,TTC,u,1,u,1,,,2026-09-01T10:00:00+00:00,,\n"
        "Leroy Merlin,LM-2,P2,LM,,EUR,TTC,u,1,u,1,,,2026-09-01T10:00:00+00:00,,\n"
    ).encode()
    body = SupplierImportService(session).preview(raw, "nat.csv").to_dict()
    assert body["valid"] is True
    assert body["rows"] == 2
    assert body["rows_with_price"] == 1
    assert body["rows_without_price"] == 1
    assert any("Catalogue sans magasin" in i for i in body["infos"])
    assert not any("agency_external_id vide" in w for w in body["warnings"])
    assert any("sans prix" in w and "aucune offre active" in w for w in body["warnings"])
    assert len(body["mapping_rows"]) == 2


def test_preview_mapping_rows_and_no_auto_variant_match(session):
    """Deux BA13 distincts restent non mappés — pas d'équivalence automatique."""
    raw = _csv(
        "POINT.P TEST,A1,Ag,1 rue,75012,Paris,48.84,2.41,LM-70505960,"
        "BA13 standard,LM,plaque,1,plaque,1,8.5,EUR,HT,10,30\n",
        "POINT.P TEST,A1,Ag,1 rue,75012,Paris,48.84,2.41,LM-HYDRO,"
        "BA13 hydrofuge,LM,plaque,1,plaque,1,9.5,EUR,HT,10,30\n",
    )
    body = SupplierImportService(session).preview(raw, "ba13.csv").to_dict()
    assert body["unmapped"] == 2
    assert all(r["product_id"] is None for r in body["mapping_rows"])


def test_explicit_mapping_persisted_and_reused(session):
    raw = _csv(
        "POINT.P TEST,A1,Ag,1 rue,75012,Paris,48.84,2.41,LM-70505960,"
        "BA13 standard,LM,plaque,1,plaque,1,8.5,EUR,HT,10,30\n",
        "POINT.P TEST,A1,Ag,1 rue,75012,Paris,48.84,2.41,LM-68587554,"
        "BA13 standard alt,LM,plaque,1,plaque,1,8.2,EUR,HT,10,30\n",
    )
    service = SupplierImportService(session)
    product = session.scalar(select(Product).where(Product.code == "PMC0001"))
    assert product is not None
    result = service.import_file(
        raw,
        "multi_map.csv",
        mappings=[
            {
                "supplier": "POINT.P TEST",
                "external_reference": "LM-70505960",
                "product_id": product.id,
            },
            {
                "supplier": "POINT.P TEST",
                "external_reference": "LM-68587554",
                "product_id": product.id,
            },
        ],
    )
    assert result.valid is True
    assert result.mapped == 2
    assert result.unmapped == 0

    sps = list(
        session.scalars(
            select(SupplierProduct).where(
                SupplierProduct.supplier_reference.in_(["LM-70505960", "LM-68587554"])
            )
        )
    )
    assert len(sps) == 2
    assert {sp.product_id for sp in sps} == {product.id}

    # Réimport sans mappings explicites → mapping persisté retrouvé
    again = service.import_file(raw, "multi_map_again.csv")
    assert again.valid is True
    preview = service.preview(raw, "multi_map_again.csv").to_dict()
    assert preview["mapped"] == 2
    assert preview["unmapped"] == 0

    offers = OfferRepository(session).for_supplier("POINT.P TEST", [product.id])
    refs = {o.supplier_reference for o in offers}
    assert "LM-70505960" in refs
    assert "LM-68587554" in refs


def test_leave_unmapped_excluded_from_comparator(session):
    raw = _csv(
        "POINT.P TEST,A1,Ag,1 rue,75012,Paris,48.84,2.41,UNMAP-REF,"
        "Produit hors map,X,u,1,u,1,3.0,EUR,HT,5,30\n"
    )
    service = SupplierImportService(session)
    result = service.import_file(raw, "unmap.csv")
    assert result.valid is True
    assert result.unmapped == 1
    sp = session.scalar(
        select(SupplierProduct).where(SupplierProduct.supplier_reference == "UNMAP-REF")
    )
    assert sp is not None
    assert sp.product_id is None
    # Aucune offre mappée pour un product_id inexistant côté unmapped
    offers = OfferRepository(session).for_supplier("POINT.P TEST", [1])
    assert "UNMAP-REF" not in {o.supplier_reference for o in offers}


def test_catalog_mapping_edit_api(client, session):
    raw = _csv(
        "POINT.P TEST,A1,Ag,1 rue,75012,Paris,48.84,2.41,EDIT-MAP-1,"
        "À mapper,X,u,1,u,1,4.0,EUR,HT,5,30\n"
    )
    service = SupplierImportService(session)
    result = service.import_file(raw, "edit_map.csv")
    catalog_id = result.catalog_id
    listing = client.get(f"/api/supplier-catalogs/{catalog_id}/mappings")
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["mapped"] is False
    sp_id = items[0]["supplier_product_id"]

    product = session.scalar(select(Product).where(Product.code == "PMC0001"))
    updated = client.put(
        f"/api/supplier-products/{sp_id}/mapping",
        json={"product_id": product.id},
    )
    assert updated.status_code == 200
    assert updated.json()["mapped"] is True
    assert updated.json()["product_id"] == product.id

    cleared = client.put(
        f"/api/supplier-products/{sp_id}/mapping",
        json={"product_id": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["mapped"] is False


def test_import_api_accepts_mappings_form(client, session):
    import json

    raw = _csv(
        "POINT.P TEST,A1,Ag,1 rue,75012,Paris,48.84,2.41,API-MAP-1,"
        "Via API,X,u,1,u,1,4.5,EUR,HT,5,30\n"
    )
    product = session.scalar(select(Product).where(Product.code == "PMC0001"))
    response = client.post(
        "/api/supplier-imports",
        files={"file": ("api_map.csv", raw, "text/csv")},
        data={
            "mappings": json.dumps(
                [
                    {
                        "supplier": "POINT.P TEST",
                        "external_reference": "API-MAP-1",
                        "product_id": product.id,
                    }
                ]
            )
        },
    )
    assert response.status_code == 200
    assert response.json()["mapped"] == 1
    assert response.json()["unmapped"] == 0
    sp = session.scalar(
        select(SupplierProduct).where(SupplierProduct.supplier_reference == "API-MAP-1")
    )
    assert sp is not None
    assert sp.product_id == product.id


def test_no_national_catalog_warning_from_parser():
    raw = (
        "supplier,product_external_reference,product_name,price,currency,tax_basis,"
        "agency_external_id\n"
        "S,R1,N,1,EUR,TTC,\n"
    ).encode()
    _c, errors, warnings = FileSupplierConnector.parse_upload(raw, filename="n.csv")
    assert errors == []
    assert not any(w["code"] == "national_catalog" for w in warnings)
