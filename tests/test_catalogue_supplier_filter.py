"""Filtre fournisseur du catalogue Admin."""

from decimal import Decimal

from sqlalchemy import select

from app.models import Product, Supplier, SupplierProduct
from app.services.admin_catalogue import AdminCatalogueService


def _product(session, code, *, category="Plâtrerie", name=None):
    row = Product(
        code=code,
        name=name or code,
        category=category,
        reference_unit="pièce",
        description=None,
        is_active=True,
        is_legacy=False,
    )
    session.add(row)
    session.flush()
    return row


def _supplier(session, name, *, active=True):
    row = session.scalar(select(Supplier).where(Supplier.name == name))
    if row is None:
        row = Supplier(name=name, source_type="file", active=active)
        session.add(row)
        session.flush()
    else:
        row.active = active
        session.flush()
    return row


def _link(session, product, supplier, ref):
    session.add(
        SupplierProduct(
            product_id=product.id,
            supplier_id=supplier.id,
            supplier_reference=ref,
            designation=ref,
            supplier_unit="pièce",
            reference_quantity=Decimal("1"),
            packaging_quantity=Decimal("1"),
            reference_unit="pièce",
            active=True,
        )
    )


def test_supplier_filter_menu_active_only_and_shared(session):
    brico = _supplier(session, "BRICO_DEPOT", active=True)
    gedimat = _supplier(session, "GEDIMAT", active=True)
    inactive = _supplier(session, "POINT.P TEST", active=False)
    only_brico = _product(session, "PMC-FILTRE-BRICO")
    only_gedimat = _product(session, "PMC-FILTRE-GEDIMAT")
    shared = _product(session, "PMC-FILTRE-SHARED", name="BA13 partagé")
    other = _product(session, "PMC-FILTRE-OTHER", category="Fixation")
    _link(session, only_brico, brico, "B-1")
    _link(session, only_brico, brico, "B-2")  # même fournisseur, 2 SP → 1 Product
    _link(session, only_gedimat, gedimat, "G-1")
    _link(session, shared, brico, "B-S")
    _link(session, shared, gedimat, "G-S")
    _link(session, other, brico, "B-X")
    session.commit()

    service = AdminCatalogueService(session)
    all_rows = service.list_products(page=1, page_size=100, legacy="exclude")
    names = [s.name for s in all_rows.suppliers]
    assert "BRICO_DEPOT" in names
    assert "GEDIMAT" in names
    assert "POINT.P TEST" not in names
    assert all(s.active for s in all_rows.suppliers)
    assert names == sorted(names)

    by_brico = service.list_products(supplier=str(brico.id), page=1, page_size=100)
    brico_codes = [item.code for item in by_brico.items if item.code.startswith("PMC-FILTRE-")]
    assert brico_codes.count("PMC-FILTRE-BRICO") == 1
    assert "PMC-FILTRE-SHARED" in brico_codes
    assert "PMC-FILTRE-GEDIMAT" not in brico_codes

    by_gedimat = service.list_products(supplier=str(gedimat.id), page=1, page_size=100)
    gedimat_codes = [item.code for item in by_gedimat.items if item.code.startswith("PMC-FILTRE-")]
    assert "PMC-FILTRE-GEDIMAT" in gedimat_codes
    assert "PMC-FILTRE-SHARED" in gedimat_codes
    assert "PMC-FILTRE-BRICO" not in gedimat_codes

    # Inactif : pas dans le menu ; filtre par id toujours possible côté SQL si besoin,
    # mais le menu ne le propose pas.
    assert inactive.id not in {s.id for s in all_rows.suppliers}


def test_supplier_filter_with_search_category_and_pagination(session):
    brico = _supplier(session, "BRICO_DEPOT")
    gedimat = _supplier(session, "GEDIMAT")
    for i in range(30):
        product = _product(
            session,
            f"PMC-PAGE-G-{i:02d}",
            category="Plâtrerie" if i < 20 else "Fixation",
            name=f"BA13 plaque {i}" if i % 2 == 0 else f"Rail {i}",
        )
        _link(session, product, gedimat, f"G-{i}")
        if i < 5:
            _link(session, product, brico, f"B-{i}")
    session.commit()

    service = AdminCatalogueService(session)
    page1 = service.list_products(supplier=str(gedimat.id), page=1, page_size=10)
    page2 = service.list_products(supplier=str(gedimat.id), page=2, page_size=10)
    assert page1.total >= 30
    assert len(page1.items) == 10
    assert len(page2.items) == 10
    assert {item.id for item in page1.items}.isdisjoint({item.id for item in page2.items})

    ba13 = service.list_products(supplier=str(gedimat.id), q="BA13", page=1, page_size=50)
    assert ba13.total >= 15
    assert all("BA13" in item.name or "BA13" in item.code for item in ba13.items)

    cat = service.list_products(
        supplier=str(gedimat.id), category="Plâtrerie", page=1, page_size=50
    )
    assert cat.total >= 20
    assert all(item.category == "Plâtrerie" for item in cat.items)


def test_supplier_filter_api_menu(client, session):
    gedimat = _supplier(session, "GEDIMAT", active=True)
    brico = _supplier(session, "BRICO_DEPOT", active=True)
    _supplier(session, "LEROY_MERLIN", active=False)
    product = _product(session, "PMC-API-GED", name="BA13 API")
    shared = _product(session, "PMC-API-SHARED", name="BA13 shared")
    _link(session, product, gedimat, "API-G")
    _link(session, shared, gedimat, "API-GS")
    _link(session, shared, brico, "API-BS")
    session.commit()
    response = client.get(
        "/api/admin/catalogue/products",
        params={"supplier": str(gedimat.id), "q": "BA13", "page_size": 50},
    )
    assert response.status_code == 200
    body = response.json()
    assert any(item["code"] == "PMC-API-GED" for item in body["items"])
    names = [s["name"] for s in body["suppliers"]]
    assert "GEDIMAT" in names
    assert "BRICO_DEPOT" in names
    assert "LEROY_MERLIN" not in names

    page = client.get("/admin/catalogue")
    assert page.status_code == 200
    html = page.text
    assert 'id="catalogue-supplier"' in html
    assert "Multi-fournisseurs" not in html
    # Options rendues côté serveur (pas seulement via JS après loadList).
    assert f'<option value="{brico.id}">BRICO_DEPOT</option>' in html
    assert f'<option value="{gedimat.id}">GEDIMAT</option>' in html
    assert "LEROY_MERLIN" not in html.split('id="catalogue-supplier"')[1].split("</select>")[0]

    by_brico = client.get(
        "/api/admin/catalogue/products",
        params={"supplier": str(brico.id), "q": "PMC-API-SHARED", "page_size": 50},
    ).json()
    by_gedimat = client.get(
        "/api/admin/catalogue/products",
        params={"supplier": str(gedimat.id), "q": "PMC-API-SHARED", "page_size": 50},
    ).json()
    assert any(i["code"] == "PMC-API-SHARED" for i in by_brico["items"])
    assert any(i["code"] == "PMC-API-SHARED" for i in by_gedimat["items"])
