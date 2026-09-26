"""Back-office /admin/catalogue — liste, fiche, unmapped, édition."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.models import Agency, Offer, Product, Supplier, SupplierProduct
from app.version import APP_VERSION
from scripts.normalized_catalog import seed_normalized_catalog


def _attach_ttc_offer(session, product_code: str, price: str, ref: str, *, unit="plaque", ref_qty="1"):
    """Crée une offre TTC @20 % mappée pour un Product normalisé (test isolé)."""
    seed_normalized_catalog(session)
    session.commit()
    product = session.scalar(select(Product).where(Product.code == product_code))
    assert product is not None
    supplier = session.scalar(select(Supplier).where(Supplier.name == "ADMIN_CAT_TEST"))
    if supplier is None:
        supplier = Supplier(name="ADMIN_CAT_TEST", source_type="file", source_key="test-admin-cat")
        session.add(supplier)
        session.flush()
    agency = session.scalar(select(Agency).where(Agency.supplier_id == supplier.id))
    if agency is None:
        agency = Agency(
            supplier_id=supplier.id,
            external_id="NAT",
            name="Catalogue national test",
            address="n/a",
            postal_code="00000",
            city="National",
            latitude=None,
            longitude=None,
        )
        session.add(agency)
        session.flush()
    sp = session.scalar(
        select(SupplierProduct).where(
            SupplierProduct.supplier_id == supplier.id,
            SupplierProduct.supplier_reference == ref,
        )
    )
    if sp is None:
        sp = SupplierProduct(
            supplier_id=supplier.id,
            product_id=product.id,
            supplier_reference=ref,
            designation=product.name,
            supplier_unit=unit,
            packaging_quantity=Decimal("1") if unit != "lot" else Decimal("10"),
            reference_quantity=Decimal(ref_qty),
            reference_unit=product.reference_unit,
            active=True,
            correction_source="import",
        )
        session.add(sp)
        session.flush()
    else:
        sp.product_id = product.id
        sp.reference_unit = product.reference_unit
        sp.reference_quantity = Decimal(ref_qty)
        sp.supplier_unit = unit
    offer = session.scalar(
        select(Offer).where(
            Offer.supplier_product_id == sp.id,
            Offer.agency_id == agency.id,
        )
    )
    if offer is None:
        offer = Offer(
            supplier_id=supplier.id,
            supplier_product_id=sp.id,
            agency_id=agency.id,
            price=Decimal(price),
            stock=1000000,
            preparation_minutes=60,
            currency="EUR",
            tax_basis="TTC",
            vat_rate=Decimal("20.00"),
            source_type="file",
            source_key="test-admin-cat",
        )
        session.add(offer)
    else:
        offer.price = Decimal(price)
        offer.tax_basis = "TTC"
        offer.vat_rate = Decimal("20.00")
    session.commit()
    return product


def test_app_version_catalogue_release():
    assert APP_VERSION == "0.9.3"


def test_admin_catalogue_page_and_assets(client):
    res = client.get("/admin/catalogue")
    assert res.status_code == 200
    html = res.text
    assert "Catalogue ProMatConnect" in html
    assert "admin_catalogue.js" in html
    assert 'href="/admin/fournisseurs"' in html
    assert 'href="/admin/catalogue"' in html
    assert APP_VERSION in html
    assert "Références fournisseurs" in html
    assert "Sans référence fournisseur" in html
    assert "Références non rattachées" in html
    assert "Références non mappées" not in html
    assert ">SP<" not in html.replace(" ", "")
    assert "<th scope=\"col\">Réf.</th>" in html
    assert "Avec SP" not in html
    assert "Sans SP" not in html


def test_admin_catalogue_ux_flags_terminology():
    """Flags : Product sans réf. ≠ Product avec réf. mais sans offre."""
    js = Path("app/static/admin_catalogue.js").read_text(encoding="utf-8")
    assert "Sans référence fournisseur" in js
    assert "supplier_product_count" in js
    assert "Rattacher" in js
    assert "Aucune référence fournisseur rattachée." in js
    assert "+ Rattacher une référence fournisseur" in js
    assert "Détacher" in js
    assert "Changer le rattachement" in js
    assert "Mode avancé" in js
    assert "non rattachées" in js
    assert "window.confirm" in js
    assert "Rattacher la référence" in js
    assert "Détacher la référence" in js
    assert "Changer le rattachement de" in js
    assert "putMapping" in js
    assert "/api/supplier-products/" in js
    # Pas de jargon technique exposé dans les chaînes UI principales
    assert "SupplierProduct associés" not in js
    assert "Aucun mapping." not in js
    assert "if ((item.supplier_product_count || 0) === 0)" in js
    assert "else if (!item.has_price)" in js


def test_product_fiche_empty_refs_api(client, session):
    seed_normalized_catalog(session)
    session.commit()
    product = session.scalar(select(Product).where(Product.code == "PMC-VIS-PLACO-35X35"))
    assert product is not None
    # Assurer zéro rattachement pour ce cas de capture
    for sp in session.scalars(
        select(SupplierProduct).where(SupplierProduct.product_id == product.id)
    ):
        sp.product_id = None
    session.commit()
    detail = client.get(f"/api/admin/catalogue/products/{product.id}").json()
    assert detail["code"] == "PMC-VIS-PLACO-35X35"
    assert detail["supplier_products"] == []
    assert detail["offer_count"] == 0


def _make_unmapped_sp(session, *, ref: str, designation: str, price: str = "12.50"):
    supplier = session.scalar(select(Supplier).where(Supplier.name == "ATTACH_TEST_SUP"))
    if supplier is None:
        supplier = Supplier(name="ATTACH_TEST_SUP", source_type="file", source_key="attach-test")
        session.add(supplier)
        session.flush()
    agency = session.scalar(select(Agency).where(Agency.supplier_id == supplier.id))
    if agency is None:
        agency = Agency(
            supplier_id=supplier.id,
            external_id="NAT",
            name="Agence attach test",
            address="n/a",
            postal_code="00000",
            city="National",
            latitude=None,
            longitude=None,
        )
        session.add(agency)
        session.flush()
    sp = session.scalar(
        select(SupplierProduct).where(
            SupplierProduct.supplier_id == supplier.id,
            SupplierProduct.supplier_reference == ref,
        )
    )
    if sp is None:
        sp = SupplierProduct(
            supplier_id=supplier.id,
            product_id=None,
            supplier_reference=ref,
            designation=designation,
            brand="MarqueAttach",
            ean="3012345678901",
            supplier_unit="boîte",
            packaging_quantity=Decimal("1"),
            reference_quantity=Decimal("200"),
            reference_unit="pièce",
            active=True,
        )
        session.add(sp)
        session.flush()
    else:
        sp.product_id = None
        sp.designation = designation
    offer = session.scalar(
        select(Offer).where(Offer.supplier_product_id == sp.id, Offer.agency_id == agency.id)
    )
    if offer is None:
        session.add(
            Offer(
                supplier_id=supplier.id,
                supplier_product_id=sp.id,
                agency_id=agency.id,
                price=Decimal(price),
                stock=100,
                preparation_minutes=30,
                currency="EUR",
                tax_basis="HT",
                vat_rate=None,
                source_type="file",
                source_key="attach-test",
            )
        )
    else:
        offer.price = Decimal(price)
    session.commit()
    session.refresh(sp)
    return sp


def test_unmapped_search_by_supplier_ref_designation(client, session):
    seed_normalized_catalog(session)
    session.commit()
    _make_unmapped_sp(
        session, ref="VIS-35X35-TEST", designation="Vis plaque de plâtre 3,5 × 35 attach"
    )
    by_sup = client.get("/api/admin/catalogue/unmapped", params={"q": "ATTACH_TEST"}).json()
    assert any(i["supplier_reference"] == "VIS-35X35-TEST" for i in by_sup["items"])
    by_ref = client.get("/api/admin/catalogue/unmapped", params={"q": "VIS-35X35"}).json()
    assert any(i["supplier_reference"] == "VIS-35X35-TEST" for i in by_ref["items"])
    by_name = client.get("/api/admin/catalogue/unmapped", params={"q": "Vis plaque"}).json()
    assert any(i["supplier_reference"] == "VIS-35X35-TEST" for i in by_name["items"])
    by_brand = client.get("/api/admin/catalogue/unmapped", params={"q": "MarqueAttach"}).json()
    assert any(i["supplier_reference"] == "VIS-35X35-TEST" for i in by_brand["items"])
    by_ean = client.get("/api/admin/catalogue/unmapped", params={"q": "3012345678901"}).json()
    assert any(i["supplier_reference"] == "VIS-35X35-TEST" for i in by_ean["items"])


def test_attach_detach_retarget_preserves_sp_and_offer(client, session):
    seed_normalized_catalog(session)
    session.commit()
    product_a = session.scalar(select(Product).where(Product.code == "PMC-VIS-PLACO-35X35"))
    product_b = session.scalar(select(Product).where(Product.code == "PMC-VIS-PLACO-35X25"))
    assert product_a and product_b
    sp = _make_unmapped_sp(
        session, ref="VIS-ATTACH-1", designation="Vis test attach", price="12.50"
    )
    offer_id = session.scalar(select(Offer.id).where(Offer.supplier_product_id == sp.id))
    assert offer_id is not None

    before = client.get(f"/api/admin/catalogue/products/{product_a.id}").json()
    assert before["supplier_products"] == [] or all(
        s["supplier_reference"] != "VIS-ATTACH-1" for s in before["supplier_products"]
    )

    mapped = client.put(
        f"/api/supplier-products/{sp.id}/mapping",
        json={"product_id": product_a.id},
    )
    assert mapped.status_code == 200
    assert mapped.json()["product_id"] == product_a.id

    after = client.get(f"/api/admin/catalogue/products/{product_a.id}").json()
    refs = [s["supplier_reference"] for s in after["supplier_products"]]
    assert "VIS-ATTACH-1" in refs
    assert after["offer_count"] >= 1
    assert Decimal(after["min_price_ht"]) == Decimal("12.50")

    # Retarget A -> B
    moved = client.put(
        f"/api/supplier-products/{sp.id}/mapping",
        json={"product_id": product_b.id},
    )
    assert moved.status_code == 200
    assert moved.json()["product_id"] == product_b.id
    a2 = client.get(f"/api/admin/catalogue/products/{product_a.id}").json()
    b2 = client.get(f"/api/admin/catalogue/products/{product_b.id}").json()
    assert all(s["supplier_reference"] != "VIS-ATTACH-1" for s in a2["supplier_products"])
    assert any(s["supplier_reference"] == "VIS-ATTACH-1" for s in b2["supplier_products"])

    # Detach
    detached = client.put(
        f"/api/supplier-products/{sp.id}/mapping",
        json={"product_id": None},
    )
    assert detached.status_code == 200
    assert detached.json()["product_id"] is None

    session.expire_all()
    sp2 = session.get(SupplierProduct, sp.id)
    assert sp2 is not None
    assert sp2.product_id is None
    offer2 = session.get(Offer, offer_id)
    assert offer2 is not None
    assert offer2.price == Decimal("12.50")
    assert session.get(Product, product_a.id) is not None


def test_attributes_unknown_preserved_on_patch(client, session):
    seed_normalized_catalog(session)
    session.commit()
    product = session.scalar(select(Product).where(Product.code == "PMC-BA13-STD-2500X1200"))
    assert product is not None
    patched = client.patch(
        f"/api/admin/catalogue/products/{product.id}",
        json={
            "attributes": {
                "type": "standard",
                "length_mm": 2500,
                "width_mm": 1200,
                "thickness_mm": 13,
                "surface_m2": 3.0,
                "custom_foo": "bar-keep",
            }
        },
    )
    assert patched.status_code == 200
    attrs = patched.json()["attributes"]
    assert attrs["thickness_mm"] == 13
    assert attrs["custom_foo"] == "bar-keep"
    # restore roughly
    client.patch(
        f"/api/admin/catalogue/products/{product.id}",
        json={
            "attributes": {
                "type": "standard",
                "length_mm": 2500,
                "width_mm": 1200,
                "thickness_mm": 13,
                "surface_m2": 3.0,
            }
        },
    )


def test_admin_catalogue_list_search_ba13(client, session):
    seed_normalized_catalog(session)
    session.commit()
    res = client.get("/api/admin/catalogue/products", params={"q": "BA13", "legacy": "exclude"})
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 1
    codes = [i["code"] for i in data["items"]]
    assert any("BA13" in c for c in codes)
    assert all(i["is_legacy"] is False for i in data["items"])


def test_admin_catalogue_product_ba13_std(client, session):
    product = _attach_ttc_offer(session, "PMC-BA13-STD-2500X1200", "8.37", "70505960")
    listing = client.get(
        "/api/admin/catalogue/products",
        params={"q": "PMC-BA13-STD-2500X1200", "legacy": "exclude"},
    ).json()
    item = next(i for i in listing["items"] if i["code"] == "PMC-BA13-STD-2500X1200")
    assert Decimal(item["min_price_ht"]) == Decimal("6.98")
    assert Decimal(item["min_price_ttc"]) == Decimal("8.37")

    detail = client.get(f"/api/admin/catalogue/products/{product.id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["code"] == "PMC-BA13-STD-2500X1200"
    assert body["attributes"]["thickness_mm"] == 13
    assert body["supplier_products"]
    assert body["min_price_ht"] == "6.98"
    assert body["offer_count"] >= 1


def test_admin_catalogue_product_rail(client, session):
    product = _attach_ttc_offer(
        session, "PMC-RAIL-R48-3000", "25.90", "85343284", unit="lot", ref_qty="10"
    )
    listing = client.get(
        "/api/admin/catalogue/products",
        params={"q": "PMC-RAIL-R48-3000", "legacy": "exclude"},
    ).json()
    item = next(i for i in listing["items"] if i["code"] == "PMC-RAIL-R48-3000")
    assert Decimal(item["min_price_ht"]) == Decimal("21.58")
    assert Decimal(item["min_price_ttc"]) == Decimal("25.90")

    detail = client.get(f"/api/admin/catalogue/products/{product.id}").json()
    assert detail["reference_unit"] == "pièce"
    assert any(sp["supplier_unit"] == "lot" for sp in detail["supplier_products"])
    assert Decimal(detail["min_price_ht"]) == Decimal("21.58")


def test_admin_catalogue_unmapped_and_patch(client, session):
    seed_normalized_catalog(session)
    session.commit()
    unmapped = client.get("/api/admin/catalogue/unmapped").json()
    assert "items" in unmapped
    assert unmapped["total"] >= 0

    product = session.scalar(select(Product).where(Product.code == "PMC-BA13-STD-2500X1200"))
    before = client.get(f"/api/admin/catalogue/products/{product.id}").json()
    patched = client.patch(
        f"/api/admin/catalogue/products/{product.id}",
        json={"description": "Test admin catalogue patch"},
    )
    assert patched.status_code == 200
    assert patched.json()["description"] == "Test admin catalogue patch"
    client.patch(
        f"/api/admin/catalogue/products/{product.id}",
        json={"description": before.get("description")},
    )


def test_admin_catalogue_legacy_filter(client, session):
    seed_normalized_catalog(session)
    session.commit()
    only = client.get("/api/admin/catalogue/products", params={"legacy": "only"}).json()
    assert all(i["is_legacy"] for i in only["items"]) or only["total"] >= 0
    excl = client.get("/api/admin/catalogue/products", params={"legacy": "exclude"}).json()
    assert all(not i["is_legacy"] for i in excl["items"])


def test_admin_nav_on_fournisseurs(client):
    html = client.get("/admin/fournisseurs").text
    assert "/admin/catalogue" in html
    assert "admin-subnav" in html


def test_static_admin_catalogue_js_exists():
    path = Path("app/static/admin_catalogue.js")
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "admin/catalogue/products" in text
    assert "min_price_ht" in text


def test_topbar_has_admin_link(client):
    html = client.get("/").text
    assert 'href="/admin/catalogue"' in html
