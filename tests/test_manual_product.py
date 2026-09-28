"""Création manuelle d'un Product PMC et association explicite."""

from decimal import Decimal

from sqlalchemy import select

from app.models import Product, Supplier, SupplierProduct


def _create(client, **extra):
    payload = {
        "name": "Plaque manuelle",
        "category": "Plaques",
        "reference_unit": "pièce",
        "description": "Créée depuis l'admin",
    }
    payload.update(extra)
    return client.post("/api/admin/catalogue/products", json=payload)


def test_create_generates_manual_code_and_is_searchable(client):
    page = client.get("/admin/catalogue")
    assert page.status_code == 200
    assert "+ Créer un produit PMC" in page.text
    response = _create(client, attributes={"thickness_mm": 13})
    assert response.status_code == 201
    body = response.json()
    assert body["code"].startswith("PMC-MAN-")
    assert len(body["code"]) == len("PMC-MAN-") + 8
    assert body["is_legacy"] is False
    assert body["is_active"] is True
    assert body["attributes"]["identity_level"] == "manual"
    assert body["attributes"]["thickness_mm"] == 13
    found = client.get("/api/products", params={"q": body["code"]}).json()
    assert any(item["code"] == body["code"] and item["id"] == body["id"] for item in found)
    print(f"AUTO {body['code']} id={body['id']}")


def test_create_accepts_manual_code_and_rejects_duplicate(client):
    created = _create(client, name="BA13 2600", code="pmc-ba13-std-2600x1200")
    assert created.status_code == 201
    assert created.json()["code"] == "PMC-BA13-STD-2600X1200"
    duplicate = _create(client, name="Autre", code="PMC-BA13-STD-2600X1200")
    assert duplicate.status_code == 409
    found = client.get("/api/products", params={"q": "PMC-BA13-STD-2600X1200"}).json()
    matches = [item for item in found if item["code"] == "PMC-BA13-STD-2600X1200"]
    assert len(matches) == 1
    invalid = _create(client, code="BA13")
    assert invalid.status_code == 400
    print("MANUAL PMC-BA13-STD-2600X1200")


def test_associate_brico_and_gedimat_without_silent_remap(client, session):
    first = _create(client, name="Cible association", code="PMC-MAN-CIBLE").json()
    second = _create(client, name="Autre PMC", code="PMC-MAN-AUTRE").json()
    session.expire_all()
    brico = Supplier(name="BRICO_DEPOT", source_type="api", active=True)
    gedimat = Supplier(name="GEDIMAT", source_type="api", active=True)
    session.add_all([brico, gedimat])
    session.flush()

    def add_sp(supplier, ref):
        row = SupplierProduct(
            supplier_id=supplier.id,
            product_id=None,
            supplier_reference=ref,
            designation=ref,
            supplier_unit="pièce",
            reference_quantity=Decimal("1"),
            packaging_quantity=Decimal("1"),
            reference_unit="pièce",
            active=True,
        )
        session.add(row)
        session.flush()
        return row

    brico_sp = add_sp(brico, "BRICO-REF-X")
    gedimat_sp = add_sp(gedimat, "GEDIMAT-REF-Y")
    session.commit()

    attached_brico = client.put(
        f"/api/supplier-products/{brico_sp.id}/mapping",
        json={"product_id": first["id"]},
    )
    attached_gedimat = client.put(
        f"/api/supplier-products/{gedimat_sp.id}/mapping",
        json={"product_id": first["id"]},
    )
    assert attached_brico.status_code == 200
    assert attached_gedimat.status_code == 200

    detail = client.get(f"/api/admin/catalogue/products/{first['id']}").json()
    linked = {item["supplier"]: item["supplier_reference"] for item in detail["supplier_products"]}
    assert linked["BRICO_DEPOT"] == "BRICO-REF-X"
    assert linked["GEDIMAT"] == "GEDIMAT-REF-Y"

    found = client.get("/api/admin/catalogue/references", params={"q": "GEDIMAT-REF-Y"}).json()
    hit = next(item for item in found["items"] if item["supplier_reference"] == "GEDIMAT-REF-Y")
    assert hit["current_product_code"] == "PMC-MAN-CIBLE"

    blocked = client.put(
        f"/api/supplier-products/{gedimat_sp.id}/mapping",
        json={"product_id": second["id"]},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "remap_confirmation_required"
    session.expire_all()
    assert session.get(SupplierProduct, gedimat_sp.id).product_id == first["id"]
    assert session.scalar(select(Product).where(Product.code == "PMC-MAN-CIBLE")).id == first["id"]

    moved = client.put(
        f"/api/supplier-products/{gedimat_sp.id}/mapping",
        json={"product_id": second["id"], "confirm_remap": True},
    )
    assert moved.status_code == 200
    assert moved.json()["product_code"] == "PMC-MAN-AUTRE"
    print("ASSOCIATED BRICO-REF-X + GEDIMAT-REF-Y ; silent remap blocked")
