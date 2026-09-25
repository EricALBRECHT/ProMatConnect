"""Édition admin unités / conditionnements + overrides manuels."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.models import Offer, Product, Supplier, SupplierProduct
from app.models.chantier import Chantier, ChantierMaterial
from app.repositories.offers import OfferRepository
from app.services.supplier_import import SupplierImportService
from app.services.units import normalize_unit, units_compatible


def test_normalize_piece_aliases():
    assert normalize_unit("piece") == "pièce"
    assert normalize_unit("pièce") == "pièce"
    assert normalize_unit("m2") == "m²"
    assert units_compatible("pièce", "piece")
    assert not units_compatible("pièce", "m")


def test_patch_product_unit_unused(client, session):
    product = session.scalar(select(Product).where(Product.code == "PMC-BA13-HYDRO-2500X1200"))
    assert product is not None
    product.reference_unit = "m²"
    session.commit()
    res = client.patch(
        f"/api/products/{product.id}/reference-unit",
        json={"reference_unit": "pièce"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["reference_unit"] == "pièce"


def test_patch_product_unit_blocked_when_used(client, session):
    product = session.scalar(select(Product).where(Product.code == "PMC-BA13-STD-2500X1200"))
    chantier = Chantier(
        nom="TEST UNIT PROTECT",
        adresse="10 rue du Chantier, 75004 Paris",
        latitude=Decimal("48.856600"),
        longitude=Decimal("2.352200"),
    )
    session.add(chantier)
    session.flush()
    session.add(
        ChantierMaterial(
            chantier_id=chantier.id, product_id=product.id, quantite=Decimal("2"), ordre=0
        )
    )
    session.commit()
    res = client.patch(
        f"/api/products/{product.id}/reference-unit",
        json={"reference_unit": "m"},
    )
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["code"] == "product_in_use"
    assert detail["chantier_count"] >= 1
    session.refresh(product)
    assert product.reference_unit == "pièce"


def test_supplier_product_conditioning_and_incompatibility(client, session):
    product = session.scalar(select(Product).where(Product.code == "PMC-RAIL-R48-3000"))
    supplier = session.scalar(select(Supplier).where(Supplier.name == "POINT.P TEST"))
    assert product is not None and supplier is not None
    sp = SupplierProduct(
        product_id=product.id,
        supplier_id=supplier.id,
        supplier_reference="PPT-COND-TEST",
        designation="Réf conditionnement test",
        supplier_unit="lot",
        packaging_quantity=Decimal("10"),
        reference_unit="pièce",
        reference_quantity=Decimal("10"),
        active=True,
        correction_source=None,
    )
    session.add(sp)
    session.flush()
    from app.models import Agency, Offer
    from datetime import datetime, timezone

    agency = session.scalar(select(Agency).where(Agency.supplier_id == supplier.id))
    session.add(
        Offer(
            supplier_id=supplier.id,
            supplier_product_id=sp.id,
            agency_id=agency.id,
            price=Decimal("25.90"),
            stock=50,
            preparation_minutes=30,
            tax_basis="TTC",
            currency="EUR",
            source_type="demo",
            observed_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        )
    )
    session.commit()

    bad = client.put(
        f"/api/supplier-products/{sp.id}/conditioning",
        json={
            "supplier_unit": "lot",
            "packaging_quantity": "10",
            "reference_unit": "m",
            "reference_quantity": "30",
        },
    )
    assert bad.status_code == 200, bad.text
    body = bad.json()
    assert body["correction_source"] == "manual"
    assert body["unit_anomaly"] is True
    assert body["unit_compatible"] is False

    session.expire_all()
    offers = OfferRepository(session).for_supplier("POINT.P TEST", [product.id])
    assert all(o.supplier_reference != "PPT-COND-TEST" for o in offers)

    ok = client.put(
        f"/api/supplier-products/{sp.id}/conditioning",
        json={
            "supplier_unit": "lot",
            "packaging_quantity": "10",
            "reference_unit": "pièce",
            "reference_quantity": "10",
        },
    )
    assert ok.status_code == 200
    assert ok.json()["unit_compatible"] is True
    session.expire_all()
    offers2 = OfferRepository(session).for_supplier("POINT.P TEST", [product.id])
    hit = [o for o in offers2 if o.supplier_reference == "PPT-COND-TEST"]
    assert hit
    assert hit[0].reference_quantity == Decimal("10")
    assert hit[0].supplier_unit == "lot"


def test_box_of_1000_pieces_conditioning(client, session):
    product = session.scalar(select(Product).where(Product.code == "PMC-VIS-PLACO-35X25"))
    supplier = session.scalar(select(Supplier).where(Supplier.name == "POINT.P TEST"))
    sp = SupplierProduct(
        product_id=product.id,
        supplier_id=supplier.id,
        supplier_reference="PPT-VIS-BOX",
        designation="Boîte vis test",
        supplier_unit="boîte",
        packaging_quantity=Decimal("1"),
        reference_unit="pièce",
        reference_quantity=Decimal("1"),
        active=True,
    )
    session.add(sp)
    session.commit()
    res = client.put(
        f"/api/supplier-products/{sp.id}/conditioning",
        json={
            "supplier_unit": "boîte",
            "packaging_quantity": "1000",
            "reference_unit": "piece",
            "reference_quantity": "1000",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["reference_unit"] == "pièce"
    assert Decimal(body["reference_quantity"]) == Decimal("1000")
    assert body["correction_source"] == "manual"


def test_manual_override_survives_reimport(client, session):
    # Nom sans rail/montant/fourrure pour permettre reference_unit=m dans le CSV.
    header = (
        "supplier,agency_external_id,agency_name,agency_address,agency_postal_code,"
        "agency_city,agency_latitude,agency_longitude,product_external_reference,"
        "product_name,brand,supplier_unit,packaging_quantity,reference_unit,"
        "reference_quantity,price,currency,tax_basis,available_quantity,"
        "preparation_minutes,product_code\n"
    )
    row = (
        "POINT.P TEST,A-OV,Ag,1 rue,75012,Paris,48.84,2.41,PPT-OVERRIDE-1,"
        "Profil métallique R48,Brand,lot,1,m,30,12.00,EUR,HT,100,30,PMC0002\n"
    )
    service = SupplierImportService(session)
    first = service.import_file((header + row).encode(), "override1.csv")
    assert first.valid, first.errors
    sp = session.scalar(
        select(SupplierProduct).where(SupplierProduct.supplier_reference == "PPT-OVERRIDE-1")
    )
    assert sp is not None
    fixed = client.put(
        f"/api/supplier-products/{sp.id}/conditioning",
        json={
            "supplier_unit": "lot",
            "packaging_quantity": "10",
            "reference_unit": "pièce",
            "reference_quantity": "10",
        },
    )
    assert fixed.status_code == 200
    row2 = (
        "POINT.P TEST,A-OV,Ag,1 rue,75012,Paris,48.84,2.41,PPT-OVERRIDE-1,"
        "Profil métallique R48,Brand,lot,1,m,30,13.00,EUR,HT,100,30,PMC0002\n"
    )
    session.expire_all()
    again = service.import_file((header + row2).encode(), "override2.csv")
    assert again.valid, again.errors
    session.expire_all()
    sp = session.scalar(
        select(SupplierProduct).where(SupplierProduct.supplier_reference == "PPT-OVERRIDE-1")
    )
    assert sp.correction_source == "manual"
    assert sp.reference_unit == "pièce"
    assert sp.reference_quantity == Decimal("10")
    assert sp.packaging_quantity == Decimal("10")
    offer = session.scalar(select(Offer).where(Offer.supplier_product_id == sp.id))
    assert offer is not None
    assert offer.price == Decimal("13.00")

    cleared = client.delete(f"/api/supplier-products/{sp.id}/conditioning-override")
    assert cleared.status_code == 200
    assert cleared.json()["correction_source"] is None
    session.expire_all()
    third = service.import_file((header + row2).encode(), "override3.csv")
    assert third.valid, third.errors
    session.expire_all()
    sp = session.scalar(
        select(SupplierProduct).where(SupplierProduct.supplier_reference == "PPT-OVERRIDE-1")
    )
    assert sp.reference_unit == "m"
    assert sp.reference_quantity == Decimal("30")
    assert sp.correction_source == "import"


def test_product_usage_endpoint(client, session):
    product = session.scalar(select(Product).where(Product.code == "PMC-RAIL-R48-3000"))
    res = client.get(f"/api/products/{product.id}/usage")
    assert res.status_code == 200
    body = res.json()
    assert body["unit_editable"] is True
    assert "pièce" in body["allowed_units"]
