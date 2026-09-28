"""Supplier.active décide la participation. Le statut d'import ne la remplace pas."""

from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.connectors.bricodepot.client import BricoDepotClient
from app.connectors.bricodepot.connector import CACHE_CONNECTOR_KEY, SUPPLIER_NAME
from app.connectors.registry import build_comparison_connectors, build_live_connectors
from app.database import Base
from app.models import Agency, Offer, Product, Supplier, SupplierImport, SupplierProduct
from app.models.gedimat_store import GedimatStore
from app.models.supplier_live_cache import SupplierOfferCache
from app.schemas.catalog import ProductRead
from app.schemas.comparison import CartLine
from app.schemas.location import ResolvedOrigin
from app.services.comparison import ComparisonService
from app.services.supplier_import import SupplierImportService
from app.services.supplier_live_cache import SupplierLiveCacheService
from tests.test_bricodepot_live_wiring import RecordingTransport, _retailer_payload


SKU = "3334160524579"


def _engine():
    engine = create_engine("sqlite:///:memory:")
    import app.models  # noqa: F401

    Base.metadata.create_all(engine)
    return engine


def _origin() -> ResolvedOrigin:
    return ResolvedOrigin(
        type="site",
        label="Amiens",
        source="test",
        latitude=49.89,
        longitude=2.30,
        citycode="80021",
        city="Amiens",
        postcode="80000",
    )


def _product(session: Session, code: str = "PMC-PART") -> Product:
    product = Product(
        code=code,
        name="Produit",
        category="Test",
        reference_unit="piece",
        description=None,
    )
    session.add(product)
    session.flush()
    return product


def _titles(session: Session, settings: Settings, product: Product, client=None) -> list[str]:
    connectors = build_comparison_connectors(
        session,
        resolved_origin=_origin(),
        settings=settings,
        brico_client=client,
    )
    response = ComparisonService(
        connectors, _origin().latitude, _origin().longitude, origin=_origin()
    ).compare(
        [CartLine(product_id=product.id, quantity=Decimal("1"))],
        {product.id: ProductRead.model_validate(product)},
    )
    return [option.title for option in response.options]


def _snapshot(session: Session) -> dict:
    return {
        "sp": int(session.scalar(select(func.count()).select_from(SupplierProduct)) or 0),
        "mapped": int(
            session.scalar(
                select(func.count())
                .select_from(SupplierProduct)
                .where(SupplierProduct.product_id.is_not(None))
            )
            or 0
        ),
        "offers": int(session.scalar(select(func.count()).select_from(Offer)) or 0),
        "cache": int(session.scalar(select(func.count()).select_from(SupplierOfferCache)) or 0),
        "mappings": [
            (row.id, row.product_id)
            for row in session.scalars(select(SupplierProduct).order_by(SupplierProduct.id))
        ],
    }


def test_brico_participation_uses_supplier_active_not_catalog():
    engine = _engine()
    with Session(engine) as session:
        product = _product(session)
        supplier = Supplier(name="BRICO_DEPOT", source_type="file", active=True)
        session.add(supplier)
        session.flush()
        session.add(
            SupplierProduct(
                supplier_id=supplier.id,
                product_id=product.id,
                supplier_reference=SKU,
                designation=SKU,
                supplier_unit="piece",
                reference_quantity=Decimal("1"),
                packaging_quantity=Decimal("1"),
                reference_unit="piece",
                active=True,
            )
        )
        catalog = SupplierImport(
            source_key="api:BRICO_DEPOT:catalog",
            filename="brico",
            supplier_name="BRICO_DEPOT",
            status="deactivated",
            active=False,
        )
        session.add(catalog)
        session.commit()

        settings = Settings(
            bricodepot_live_enabled=True,
            bricodepot_max_stores=1,
            gedimat_live_enabled=False,
            brico_live_cache_ttl_seconds=1800,
        )
        cache = SupplierLiveCacheService(session, settings)
        cache.put_stores(CACHE_CONNECTOR_KEY, "80021", [_retailer_payload(entity_id=10, name="VILLETANEUSE")])
        cache.put_offer(
            CACHE_CONNECTOR_KEY,
            "10",
            SKU,
            price_ht=Decimal("7.50"),
            price_ttc=Decimal("9.00"),
            stock_quantity=4,
            is_salable=True,
            is_offer_available=True,
            price_ttl_s=1800,
            stock_ttl_s=1800,
        )
        session.commit()
        before = _snapshot(session)
        transport = RecordingTransport([])
        client = BricoDepotClient(transport=transport)

        live = build_live_connectors(
            session, resolved_origin=_origin(), settings=settings, brico_client=client
        )
        assert [c.connector_key for c in live] == ["api:BRICO_DEPOT"]
        offers = live[0].get_offers([product.id])
        assert transport.calls == []
        assert len(offers) == 1
        assert offers[0].price == Decimal("7.50")
        assert any(title == f"Tout chez {SUPPLIER_NAME}" for title in _titles(session, settings, product, client))

        SupplierImportService(session).set_supplier_active(supplier.id, False)
        assert session.get(SupplierImport, catalog.id).active is False
        inactive = build_live_connectors(
            session, resolved_origin=_origin(), settings=settings, brico_client=client
        )
        assert inactive == []
        assert transport.calls == []
        titles = _titles(session, settings, product, client)
        assert f"Tout chez {SUPPLIER_NAME}" not in titles
        assert "Tout chez BRICO_DEPOT" not in titles
        assert _snapshot(session) == before

        SupplierImportService(session).set_supplier_active(supplier.id, True)
        restored = build_live_connectors(
            session, resolved_origin=_origin(), settings=settings, brico_client=client
        )
        assert [c.connector_key for c in restored] == ["api:BRICO_DEPOT"]
        again = restored[0].get_offers([product.id])
        assert transport.calls == []
        assert len(again) == 1
        assert again[0].price == Decimal("7.50")
        assert _snapshot(session) == before


def test_gedimat_participation_follows_supplier_active():
    engine = _engine()
    with Session(engine) as session:
        product = _product(session, "PMC-GED")
        supplier = Supplier(name="GEDIMAT", source_type="api", active=True)
        session.add(supplier)
        session.flush()
        session.add(
            SupplierProduct(
                supplier_id=supplier.id,
                product_id=product.id,
                supplier_reference="SKU1",
                designation="Vis",
                supplier_unit="piece",
                reference_quantity=Decimal("1"),
                packaging_quantity=Decimal("1"),
                reference_unit="piece",
                active=True,
            )
        )
        session.add(
            GedimatStore(
                gedimat_id=10,
                algolia_store_id=101,
                name="Proche",
                address="rue",
                postal_code="80000",
                city="Proche",
                latitude=Decimal("49.90"),
                longitude=Decimal("2.31"),
                ecommerce=True,
                store_type="MAG_ECOMMERCE",
                active=True,
            )
        )
        session.commit()
        settings = Settings(
            gedimat_live_enabled=True,
            gedimat_live_cache_ttl_seconds=1800,
            bricodepot_live_enabled=False,
        )
        cache = SupplierLiveCacheService(session, settings)
        cache.put_offer(
            "GEDIMAT",
            "101",
            "SKU1",
            price_ht=Decimal("11.00"),
            price_ttc=Decimal("13.20"),
            stock_quantity=2,
            stock_status="available",
            extras={"fulfillment": "available"},
            price_ttl_s=1800,
            stock_ttl_s=1800,
        )
        session.commit()
        before = _snapshot(session)

        class Quiet:
            def __init__(self):
                self.calls = 0

            def lookup_skus(self, store_id, skus):
                self.calls += 1
                return {}

        client = Quiet()
        live = build_live_connectors(
            session, resolved_origin=_origin(), settings=settings, gedimat_client=client
        )
        assert [c.connector_key for c in live] == ["api:GEDIMAT"]
        offers = live[0].get_offers([product.id])
        assert client.calls == 0
        assert len(offers) == 1
        assert offers[0].price == Decimal("11.00")

        supplier.active = False
        session.commit()
        assert (
            build_live_connectors(
                session, resolved_origin=_origin(), settings=settings, gedimat_client=client
            )
            == []
        )
        assert client.calls == 0
        assert "Tout chez GEDIMAT" not in _titles(session, settings, product)
        assert _snapshot(session) == before

        supplier.active = True
        session.commit()
        back = build_live_connectors(
            session, resolved_origin=_origin(), settings=settings, gedimat_client=client
        )
        assert [c.connector_key for c in back] == ["api:GEDIMAT"]
        assert back[0].get_offers([product.id])[0].price == Decimal("11.00")
        assert client.calls == 0


def test_inactive_file_supplier_keeps_offers_out_of_comparison():
    engine = _engine()
    with Session(engine) as session:
        product = _product(session, "PMC-FILE")
        supplier = Supplier(name="LEROY_MERLIN", source_type="file", active=False)
        session.add(supplier)
        session.flush()
        agency = Agency(
            supplier_id=supplier.id,
            name="Catalogue",
            address="1 rue",
            postal_code="75001",
            city="Paris",
            latitude=Decimal("48.860000"),
            longitude=Decimal("2.340000"),
        )
        session.add(agency)
        session.flush()
        sp = SupplierProduct(
            supplier_id=supplier.id,
            product_id=product.id,
            supplier_reference="LM-1",
            designation="Ref",
            supplier_unit="piece",
            reference_quantity=Decimal("1"),
            packaging_quantity=Decimal("1"),
            reference_unit="piece",
            active=True,
        )
        session.add(sp)
        session.flush()
        session.add(
            Offer(
                supplier_id=supplier.id,
                supplier_product_id=sp.id,
                agency_id=agency.id,
                price=Decimal("12.50"),
                stock=3,
                preparation_minutes=30,
                tax_basis="HT",
                vat_rate=Decimal("20"),
            )
        )
        session.commit()
        settings = Settings(bricodepot_live_enabled=False, gedimat_live_enabled=False)
        names = [
            c.supplier_name
            for c in build_comparison_connectors(
                session, resolved_origin=_origin(), settings=settings
            )
        ]
        assert "LEROY_MERLIN" not in names
        assert "Tout chez LEROY_MERLIN" not in _titles(session, settings, product)
        assert session.scalar(select(func.count()).select_from(Offer)) == 1

        supplier.active = True
        session.commit()
        connectors = build_comparison_connectors(
            session, resolved_origin=_origin(), settings=settings
        )
        offers = [o for c in connectors for o in c.get_offers([product.id])]
        assert any(o.supplier == "LEROY_MERLIN" and o.price == Decimal("12.50") for o in offers)


def test_test_supplier_follows_active_flag_not_its_name():
    engine = _engine()
    with Session(engine) as session:
        product = _product(session, "PMC-TEST")
        demo = Supplier(name="POINT.P TEST", source_type="file", active=True)
        brico = Supplier(name="BRICO_DEPOT", source_type="file", active=True)
        session.add_all([demo, brico])
        session.flush()
        agency = Agency(
            supplier_id=demo.id,
            name="Agence",
            address="1 rue",
            postal_code="75001",
            city="Paris",
            latitude=Decimal("48.860000"),
            longitude=Decimal("2.340000"),
        )
        session.add(agency)
        session.flush()
        sp = SupplierProduct(
            supplier_id=demo.id,
            product_id=product.id,
            supplier_reference="PPT-1",
            designation="Ref",
            supplier_unit="piece",
            reference_quantity=Decimal("1"),
            packaging_quantity=Decimal("1"),
            reference_unit="piece",
            active=True,
        )
        session.add(sp)
        session.flush()
        session.add(
            Offer(
                supplier_id=demo.id,
                supplier_product_id=sp.id,
                agency_id=agency.id,
                price=Decimal("4.00"),
                stock=2,
                preparation_minutes=10,
                tax_basis="HT",
                vat_rate=Decimal("20"),
            )
        )
        session.commit()
        settings = Settings(
            bricodepot_live_enabled=True,
            bricodepot_max_stores=1,
            gedimat_live_enabled=False,
        )
        transport = RecordingTransport([])
        client = BricoDepotClient(transport=transport)
        names = [
            c.supplier_name
            for c in build_comparison_connectors(
                session, resolved_origin=_origin(), settings=settings, brico_client=client
            )
        ]
        assert "POINT.P TEST" in names
        assert SUPPLIER_NAME in names
        point = next(c for c in build_comparison_connectors(
            session, resolved_origin=_origin(), settings=settings, brico_client=client
        ) if c.supplier_name == "POINT.P TEST")
        assert point.get_offers([product.id])[0].price == Decimal("4.00")

        demo.active = False
        session.commit()
        hidden = [
            c.supplier_name
            for c in build_comparison_connectors(
                session, resolved_origin=_origin(), settings=settings, brico_client=client
            )
        ]
        assert "POINT.P TEST" not in hidden
        assert SUPPLIER_NAME in hidden


def test_catalog_reference_count_uses_supplier_products():
    engine = _engine()
    with Session(engine) as session:
        product = _product(session, "PMC-CAT")
        supplier = Supplier(name="GEDIMAT", source_type="api", active=True)
        session.add(supplier)
        session.flush()
        catalog = SupplierImport(
            source_key="api:GEDIMAT:catalog",
            filename="gedimat.json",
            supplier_name="GEDIMAT",
            status="imported",
            active=True,
        )
        legacy = SupplierImport(
            source_key="import-legacy",
            filename="legacy.csv",
            supplier_name="LEROY_MERLIN",
            status="imported",
            active=True,
        )
        session.add_all([catalog, legacy])
        session.flush()
        session.add(
            SupplierProduct(
                supplier_id=supplier.id,
                product_id=product.id,
                supplier_reference="A",
                designation="A",
                supplier_unit="piece",
                reference_quantity=Decimal("1"),
                packaging_quantity=Decimal("1"),
                reference_unit="piece",
                introduced_by_catalog_id=catalog.id,
                active=True,
            )
        )
        legacy_supplier = Supplier(name="LEROY_MERLIN", source_type="file", active=True)
        session.add(legacy_supplier)
        session.flush()
        agency = Agency(
            supplier_id=legacy_supplier.id,
            name="Nat",
            address="1 rue",
            postal_code="75001",
            city="Paris",
        )
        session.add(agency)
        session.flush()
        sp = SupplierProduct(
            supplier_id=legacy_supplier.id,
            product_id=product.id,
            supplier_reference="L",
            designation="L",
            supplier_unit="piece",
            reference_quantity=Decimal("1"),
            packaging_quantity=Decimal("1"),
            reference_unit="piece",
            active=True,
        )
        session.add(sp)
        session.flush()
        session.add(
            Offer(
                supplier_id=legacy_supplier.id,
                supplier_product_id=sp.id,
                agency_id=agency.id,
                catalog_id=legacy.id,
                price=Decimal("3"),
                stock=1,
                preparation_minutes=10,
                tax_basis="HT",
            )
        )
        session.commit()
        listed = {row["source_key"]: row for row in SupplierImportService(session).list_catalogs()}
        assert listed["api:GEDIMAT:catalog"]["references"] == 1
        assert listed["api:GEDIMAT:catalog"]["offers"] == 0
        assert listed["import-legacy"]["references"] == 1


def test_admin_toggle_does_not_drop_seed_rows(client):
    sources = client.get("/api/supplier-sources").json()
    point = next(row for row in sources if row["name"] == "POINT.P TEST")
    assert point["active"] is True
    assert point["references"] == point["mapped"]
    assert point["references"] > 0
    page = client.get("/admin/fournisseurs").text
    assert "Statut : active" not in page
    assert "Participation : Actif" in page
    assert "références" in page
    assert "Import actif" in page or "Aucun import" in page

    before_refs = point["references"]
    off = client.post(f"/api/suppliers/{point['id']}/deactivate")
    assert off.status_code == 200
    assert off.json()["active"] is False
    listed = client.get("/api/supplier-sources").json()
    again = next(row for row in listed if row["id"] == point["id"])
    assert again["active"] is False
    assert again["references"] == before_refs
    assert again["mapped"] == point["mapped"]
    inactive_page = client.get("/admin/fournisseurs").text
    assert "Participation : Inactif" in inactive_page

    on = client.post(f"/api/suppliers/{point['id']}/activate")
    assert on.status_code == 200
    assert on.json()["active"] is True
