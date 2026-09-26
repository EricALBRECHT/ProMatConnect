"""Édition admin unités / conditionnements + overrides manuels."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.models import Agency, Offer, Product, Supplier, SupplierProduct
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


def _sp_without_offer(session, *, ref: str = "NO-OFFER-COND"):
    product = session.scalar(select(Product).where(Product.code == "PMC-BA13-STD-2500X1200"))
    supplier = session.scalar(select(Supplier).where(Supplier.name == "POINT.P TEST"))
    assert product and supplier
    sp = session.scalar(
        select(SupplierProduct).where(
            SupplierProduct.supplier_id == supplier.id,
            SupplierProduct.supplier_reference == ref,
        )
    )
    if sp is None:
        sp = SupplierProduct(
            product_id=product.id,
            supplier_id=supplier.id,
            supplier_reference=ref,
            designation="SP sans offre test",
            supplier_unit="plaque",
            packaging_quantity=Decimal("1"),
            reference_unit="pièce",
            reference_quantity=Decimal("1"),
            active=True,
            correction_source=None,
        )
        session.add(sp)
        session.commit()
        session.refresh(sp)
    else:
        sp.supplier_unit = "plaque"
        sp.packaging_quantity = Decimal("1")
        sp.reference_unit = "pièce"
        sp.reference_quantity = Decimal("1")
        sp.correction_source = None
        session.commit()
    return product, sp


def test_conditioning_persists_all_fields_and_reread(client, session):
    product, sp = _sp_without_offer(session, ref="NO-OFFER-PERSIST")
    before_offers = session.scalars(select(Offer).where(Offer.supplier_product_id == sp.id)).all()
    assert before_offers == []

    res = client.put(
        f"/api/supplier-products/{sp.id}/conditioning",
        json={
            "supplier_unit": "plaque_test",
            "packaging_quantity": "2.5",
            "reference_unit": "pièce",
            "reference_quantity": "3",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["supplier_unit"] == "plaque_test"
    assert Decimal(body["packaging_quantity"]) == Decimal("2.5")
    assert body["reference_unit"] == "pièce"
    assert Decimal(body["reference_quantity"]) == Decimal("3")
    assert body["correction_source"] == "manual"
    assert body["has_offer"] is False
    assert body["offers_updated"] is False

    session.expire_all()
    db_sp = session.get(SupplierProduct, sp.id)
    assert db_sp.supplier_unit == "plaque_test"
    assert db_sp.packaging_quantity == Decimal("2.5")
    assert db_sp.reference_unit == "pièce"
    assert db_sp.reference_quantity == Decimal("3")
    assert db_sp.correction_source == "manual"

    detail = client.get(f"/api/admin/catalogue/products/{product.id}").json()
    hit = next(s for s in detail["supplier_products"] if s["supplier_product_id"] == sp.id)
    assert hit["supplier_unit"] == "plaque_test"
    assert Decimal(hit["packaging_quantity"]) == Decimal("2.5")
    assert hit["reference_unit"] == "pièce"
    assert Decimal(hit["reference_quantity"]) == Decimal("3")
    assert hit["correction_source"] == "manual"
    assert hit["offers"] == []

    # Aucune Offer créée implicitement
    after_offers = session.scalars(select(Offer).where(Offer.supplier_product_id == sp.id)).all()
    assert after_offers == []


def test_conditioning_with_offer_persists_price_tax_vat(client, session):
    product = session.scalar(select(Product).where(Product.code == "PMC-BA13-STD-2500X1200"))
    supplier = session.scalar(select(Supplier).where(Supplier.name == "POINT.P TEST"))
    agency = session.scalar(select(Agency).where(Agency.supplier_id == supplier.id))
    assert product and supplier and agency
    sp = SupplierProduct(
        product_id=product.id,
        supplier_id=supplier.id,
        supplier_reference="WITH-OFFER-TAX",
        designation="SP avec offre",
        supplier_unit="plaque",
        packaging_quantity=Decimal("1"),
        reference_unit="pièce",
        reference_quantity=Decimal("1"),
        active=True,
    )
    session.add(sp)
    session.flush()
    offer = Offer(
        supplier_id=supplier.id,
        supplier_product_id=sp.id,
        agency_id=agency.id,
        price=Decimal("8.00"),
        stock=10,
        preparation_minutes=30,
        tax_basis="HT",
        vat_rate=None,
        currency="EUR",
        source_type="demo",
    )
    session.add(offer)
    session.commit()

    res = client.put(
        f"/api/supplier-products/{sp.id}/conditioning",
        json={
            "supplier_unit": "plaque",
            "packaging_quantity": "1",
            "reference_unit": "pièce",
            "reference_quantity": "1",
            "price": "9.50",
            "tax_basis": "TTC",
            "vat_rate": "20",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["has_offer"] is True
    assert body["offers_updated"] is True
    assert Decimal(body["price"]) == Decimal("9.50")
    assert body["tax_basis"] == "TTC"
    assert Decimal(body["vat_rate"]) == Decimal("20")

    session.expire_all()
    db_offer = session.get(Offer, offer.id)
    assert db_offer.price == Decimal("9.50")
    assert db_offer.tax_basis == "TTC"
    assert db_offer.vat_rate == Decimal("20.00")

    detail = client.get(f"/api/admin/catalogue/products/{product.id}").json()
    hit = next(s for s in detail["supplier_products"] if s["supplier_product_id"] == sp.id)
    assert hit["offers"]
    assert Decimal(hit["offers"][0]["price"]) == Decimal("9.50")
    assert hit["offers"][0]["tax_basis"] == "TTC"
    assert Decimal(hit["offers"][0]["vat_rate"]) == Decimal("20.00")


def test_tax_fields_without_offer_do_not_create_offer(client, session):
    _, sp = _sp_without_offer(session, ref="NO-OFFER-TAX-IGN")
    res = client.put(
        f"/api/supplier-products/{sp.id}/conditioning",
        json={
            "supplier_unit": "plaque",
            "packaging_quantity": "1",
            "reference_unit": "pièce",
            "reference_quantity": "1",
            "price": "99.99",
            "tax_basis": "TTC",
            "vat_rate": "20",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["has_offer"] is False
    assert body["offers_updated"] is False
    assert body["price"] is None
    assert session.scalars(select(Offer).where(Offer.supplier_product_id == sp.id)).all() == []


def test_catalogue_conditioning_modal_payload_contract():
    """Le front n'envoie prix/TVA que s'il existe une offre ; feedback succès présent."""
    js = Path("app/static/admin_catalogue.js").read_text(encoding="utf-8")
    assert "Aucune offre associée" in js
    assert "Modifications enregistrées" in js
    assert "hasOffer" in js
    assert "payload.tax_basis" in js
    assert "data-save-btn" in js
    assert 'method="dialog"' not in js.split("catalogue-sp-form")[1].split("</dialog>")[0]
