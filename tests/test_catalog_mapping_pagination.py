"""Pagination serveur du mapping catalogue."""

from decimal import Decimal

from sqlalchemy import select

from app.models import Product, Supplier, SupplierImport, SupplierProduct
from app.services.supplier_import import SupplierImportService


def _seed_catalog(session, *, n: int = 120, supplier_name: str = "BRICO_DEPOT"):
    supplier = session.scalar(select(Supplier).where(Supplier.name == supplier_name))
    if supplier is None:
        supplier = Supplier(name=supplier_name, source_type="file", active=True)
        session.add(supplier)
        session.flush()
    catalog = SupplierImport(
        source_key=f"test-map-page-{supplier_name.lower()}",
        filename=f"{supplier_name}.csv",
        supplier_name=supplier_name,
        status="imported",
        active=True,
        rows=n,
    )
    session.add(catalog)
    session.flush()
    product = Product(
        code="PMC-MAP-PAGE",
        name="Produit page",
        category="Test",
        reference_unit="pièce",
        description=None,
    )
    session.add(product)
    session.flush()
    for i in range(n):
        mapped = i % 3 == 0
        session.add(
            SupplierProduct(
                supplier_id=supplier.id,
                product_id=product.id if mapped else None,
                supplier_reference=f"REF-{i:04d}",
                designation=f"Pièce BA13 {i}" if i % 5 == 0 else f"Article {i}",
                supplier_unit="pièce",
                reference_quantity=Decimal("1"),
                packaging_quantity=Decimal("1"),
                reference_unit="pièce",
                introduced_by_catalog_id=catalog.id,
                active=True,
                correction_source="manual" if mapped else None,
            )
        )
    session.commit()
    return catalog, product


def test_mapping_pagination_pages_and_sizes(session):
    catalog, _ = _seed_catalog(session, n=120)
    service = SupplierImportService(session)
    first = service.list_catalog_mappings(catalog.id, page=1, page_size=50)
    assert first["total"] == 120
    assert first["catalog_total"] == 120
    assert first["page"] == 1
    assert first["page_size"] == 50
    assert first["pages"] == 3
    assert first["range_start"] == 1
    assert first["range_end"] == 50
    assert len(first["items"]) == 50
    assert first["items"][0]["external_reference"] == "REF-0000"

    second = service.list_catalog_mappings(catalog.id, page=2, page_size=50)
    assert second["page"] == 2
    assert second["range_start"] == 51
    assert second["range_end"] == 100
    assert first["items"][0]["supplier_product_id"] != second["items"][0]["supplier_product_id"]

    last = service.list_catalog_mappings(catalog.id, page=3, page_size=50)
    assert last["page"] == 3
    assert last["range_start"] == 101
    assert last["range_end"] == 120
    assert len(last["items"]) == 20

    small = service.list_catalog_mappings(catalog.id, page=1, page_size=25)
    assert small["pages"] == 5
    assert len(small["items"]) == 25
    big = service.list_catalog_mappings(catalog.id, page=1, page_size=100)
    assert big["pages"] == 2
    assert len(big["items"]) == 100


def test_mapping_filters_then_paginate(session):
    catalog, product = _seed_catalog(session, n=90, supplier_name="GEDIMAT")
    other = Supplier(name="OTHER", source_type="file", active=True)
    session.add(other)
    session.flush()
    session.add(
        SupplierProduct(
            supplier_id=other.id,
            product_id=None,
            supplier_reference="OTHER-1",
            designation="Autre",
            supplier_unit="pièce",
            reference_quantity=Decimal("1"),
            packaging_quantity=Decimal("1"),
            reference_unit="pièce",
            introduced_by_catalog_id=catalog.id,
            active=True,
        )
    )
    session.commit()
    service = SupplierImportService(session)

    by_supplier = service.list_catalog_mappings(
        catalog.id, page=1, page_size=50, supplier="GEDIMAT"
    )
    assert by_supplier["total"] == 90
    assert by_supplier["catalog_total"] == 91
    assert all(item["supplier"] == "GEDIMAT" for item in by_supplier["items"])

    search = service.list_catalog_mappings(catalog.id, page=1, page_size=25, q="BA13")
    assert search["total"] == 18  # every 5th of 90
    assert search["pages"] == 1
    assert all("BA13" in item["name"] for item in search["items"])

    mapped = service.list_catalog_mappings(
        catalog.id, page=1, page_size=50, mapping="mapped", supplier="GEDIMAT"
    )
    assert mapped["total"] == 30
    assert all(item["product_id"] is not None for item in mapped["items"])

    unmapped = service.list_catalog_mappings(
        catalog.id, page=1, page_size=50, mapping="unmapped", supplier="GEDIMAT"
    )
    assert unmapped["total"] == 60
    assert unmapped["pages"] == 2
    assert all(item["product_id"] is None for item in unmapped["items"])
    assert unmapped["mapped"] == 30
    assert unmapped["unmapped"] == 60
    assert unmapped["catalog_mapped"] + unmapped["catalog_unmapped"] == unmapped["catalog_total"]

    page2 = service.list_catalog_mappings(
        catalog.id, page=2, page_size=50, mapping="unmapped", supplier="GEDIMAT"
    )
    assert page2["page"] == 2
    assert len(page2["items"]) == 10

    # Mapping since a paginated unmapped page removes the row from that filter.
    target = unmapped["items"][0]
    service.set_supplier_product_mapping(
        target["supplier_product_id"], product.id, confirm_remap=True
    )
    after = service.list_catalog_mappings(
        catalog.id, page=1, page_size=50, mapping="unmapped", supplier="GEDIMAT"
    )
    assert after["total"] == 59
    assert after["catalog_unmapped"] == unmapped["catalog_unmapped"] - 1
    assert all(
        item["supplier_product_id"] != target["supplier_product_id"] for item in after["items"]
    )

    with_price = service.list_catalog_mappings(
        catalog.id, page=1, page_size=50, price="with_price"
    )
    without_price = service.list_catalog_mappings(
        catalog.id, page=1, page_size=50, price="without_price"
    )
    assert with_price["total"] + without_price["total"] == with_price["catalog_total"]
    assert len(with_price["items"]) <= 50
    assert len(without_price["items"]) <= 50


def test_mapping_api_preserves_query_params(client, session):
    catalog, _ = _seed_catalog(session, n=60, supplier_name="LEROY_MERLIN")
    first = client.get(
        f"/api/supplier-catalogs/{catalog.id}/mappings",
        params={"page": 2, "page_size": 25, "mapping": "unmapped"},
    )
    assert first.status_code == 200
    body = first.json()
    assert body["page"] == 2
    assert body["page_size"] == 25
    assert body["mapping"] == "unmapped"
    assert body["total"] == 40
    assert body["pages"] == 2
    assert len(body["items"]) == 15
    assert all(item["product_id"] is None for item in body["items"])
    assert body["catalog_total"] == 60
    # Compteurs catalogue ≠ taille de page
    assert body["catalog_total"] != len(body["items"])
    assert body["catalog_mapped"] + body["catalog_unmapped"] == body["catalog_total"]

    search = client.get(
        f"/api/supplier-catalogs/{catalog.id}/mappings",
        params={"page": 1, "page_size": 50, "q": "BA13"},
    )
    assert search.status_code == 200
    searched = search.json()
    assert searched["q"] == "BA13"
    assert searched["total"] == 12
    assert searched["catalog_total"] == 60
    assert all("BA13" in item["name"] for item in searched["items"])

    page = client.get("/admin/fournisseurs")
    assert page.status_code == 200
    assert "admin_suppliers.js" in page.text
    # Cache-bust mtime présent (pas seulement APP_VERSION figée)
    assert "admin_suppliers.js?v=" in page.text
