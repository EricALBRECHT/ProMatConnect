"""Magasins Gedimat proches : sélection, offres multiples, cache, arrêts."""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.connectors.base import AgencyData, ConnectorOffer
from app.connectors.gedimat.connector import CACHE_CONNECTOR_KEY, GedimatConnector, GedimatLiveStore
from app.connectors.registry import build_live_connectors
from app.database import Base
from app.models import Product, Supplier, SupplierProduct
from app.models.gedimat_store import GedimatStore
from app.schemas.catalog import ProductRead
from app.schemas.comparison import CartLine
from app.schemas.location import ResolvedOrigin
from app.services.comparison import ComparisonService
from app.services.gedimat_stores import nearest_ecommerce_gedimat_stores
from app.services.supplier_live_cache import SupplierLiveCacheService


class _QuietClient:
    def __init__(self):
        self.calls = 0

    def lookup_skus(self, store_id, skus):
        self.calls += 1
        return {}


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    import app.models  # noqa: F401

    Base.metadata.create_all(engine)
    return Session(engine)


def _origin() -> ResolvedOrigin:
    return ResolvedOrigin(
        type="site",
        label="Chantier",
        source="test",
        latitude=49.0,
        longitude=2.0,
    )


def _store(gedimat_id, algolia, name, lat, lon, ecommerce=True):
    return GedimatStore(
        gedimat_id=gedimat_id,
        algolia_store_id=algolia,
        name=name,
        address="rue",
        postal_code="80000",
        city=name,
        latitude=Decimal(str(lat)),
        longitude=Decimal(str(lon)),
        ecommerce=ecommerce,
        store_type="MAG_ECOMMERCE" if ecommerce else "MAG_NONECOMMERCE",
        active=True,
    )


def test_nearest_keeps_only_ecommerce_with_algolia_id():
    session = _session()
    session.add_all(
        [
            _store(1, 11, "Loin", 48.0, 2.0),
            _store(2, 22, "Proche", 49.05, 2.01),
            _store(3, None, "Sans catalogue", 49.01, 2.0),
            _store(4, 1, "Vitrine", 49.02, 2.0, ecommerce=False),
            _store(5, 55, "Deuxième", 49.2, 2.1),
        ]
    )
    session.commit()
    picked = nearest_ecommerce_gedimat_stores(session, 49.0, 2.0, limit=2)
    assert [row.gedimat_id for row in picked] == [2, 5]
    assert all(row.algolia_store_id for row in picked)
    assert all(row.ecommerce for row in picked)


def test_registry_uses_nearest_ecommerce_stores():
    session = _session()
    session.add_all(
        [
            _store(2069, 2069, "Nesle", 49.76, 2.91),
            _store(2603, 2525, "Breuilpont", 48.95, 1.41),
            _store(2460, None, "Cayreyre", 49.1, 2.0, ecommerce=False),
        ]
    )
    session.commit()
    settings = Settings(
        gedimat_live_enabled=True,
        gedimat_nearest_store_limit=5,
        bricodepot_live_enabled=False,
    )
    connectors = build_live_connectors(
        session,
        resolved_origin=_origin(),
        settings=settings,
        gedimat_client=_QuietClient(),
    )
    assert len(connectors) == 1
    assert connectors[0].connector_key == "api:GEDIMAT"
    assert {store.gedimat_id for store in connectors[0].stores} == {2069, 2603}
    assert 2460 not in {store.gedimat_id for store in connectors[0].stores}


def _mapped(session: Session):
    supplier = Supplier(name="GEDIMAT", source_type="api", source_key="api:GEDIMAT")
    product = Product(code="PMC-VIS-TEST", name="Vis", category="VIS", reference_unit="pièce")
    session.add_all([supplier, product])
    session.flush()
    session.add(
        SupplierProduct(
            product_id=product.id,
            supplier_id=supplier.id,
            supplier_reference="SKU1",
            designation="Vis",
            supplier_unit="pièce",
            reference_quantity=Decimal("1"),
            packaging_quantity=Decimal("1"),
            reference_unit="pièce",
            active=True,
        )
    )
    session.commit()
    return product


def test_same_reference_two_stores_and_cache_skips_network():
    session = _session()
    product = _mapped(session)
    settings = Settings(gedimat_live_cache_ttl_seconds=1800)
    cache = SupplierLiveCacheService(session, settings)
    for store_key, price in (("101", "10.00"), ("202", "8.00")):
        cache.put_offer(
            CACHE_CONNECTOR_KEY,
            store_key,
            "SKU1",
            price_ht=Decimal(price),
            price_ttc=Decimal(price),
            currency="EUR",
            stock_quantity=4,
            stock_status="available",
            extras={"fulfillment": "available"},
            update_price=True,
            update_stock=True,
            price_ttl_s=1800,
            stock_ttl_s=1800,
        )
    session.commit()
    client = _QuietClient()
    connector = GedimatConnector(
        session,
        stores=[
            GedimatLiveStore(10, 101, "A", "a", "80000", "A", 49.1, 2.0),
            GedimatLiveStore(20, 202, "B", "b", "80000", "B", 49.3, 2.2),
        ],
        client=client,
        cache=cache,
        settings=settings,
    )
    offers = connector.get_offers([product.id])
    assert client.calls == 0
    assert len(offers) == 2
    assert {offer.agency.agency_key for offer in offers} == {"gedimat:10", "gedimat:20"}
    assert {offer.agency.external_id for offer in offers} == {"101", "202"}
    assert {offer.supplier_reference for offer in offers} == {"SKU1"}


def _offer(product_id, agency_id, name, lat, price, fulfillment, stock):
    return ConnectorOffer(
        supplier="GEDIMAT",
        agency=AgencyData(
            id=agency_id,
            name=name,
            address="rue",
            postal_code="80000",
            city=name,
            latitude=lat,
            longitude=2.0,
            external_id=str(agency_id + 1000),
            agency_key=f"gedimat:{agency_id}",
        ),
        product_id=product_id,
        supplier_reference="SKU1",
        supplier_unit="pièce",
        reference_quantity=Decimal("1"),
        reference_unit="pièce",
        price=Decimal(price),
        tax_basis="HT",
        vat_rate=Decimal("20"),
        stock=stock,
        preparation_minutes=0,
        updated_at=datetime.now(timezone.utc),
        fulfillment=fulfillment,
    )


class _FixedConnector:
    supplier_name = "GEDIMAT"
    connector_key = "api:GEDIMAT"

    def __init__(self, offers):
        self.offers = offers

    def get_offers(self, product_ids):
        return [offer for offer in self.offers if offer.product_id in product_ids]


def test_unavailable_nearest_store_does_not_drop_the_next_one():
    product = ProductRead(
        id=1,
        code="PMC-VIS-TEST",
        name="Vis",
        category="VIS",
        reference_unit="pièce",
        description=None,
    )
    near = _offer(1, 10, "Proche", 49.01, "5.00", "unavailable", 0)
    far = _offer(1, 20, "Suivant", 49.20, "9.00", "available", 3)
    service = ComparisonService([_FixedConnector([near, far])], 49.0, 2.0, origin=_origin())
    result = service.compare([CartLine(product_id=1, quantity=Decimal("1"))], {1: product})
    line = result.options[-1].available[0]
    assert line.agency_key == "gedimat:20"
    assert line.availability == "available"
    assert {item.key for item in result.strategies} == {
        "single_stop",
        "minimum_materials",
        "best_compromise",
    }
    single = next(item for item in result.strategies if item.key == "single_stop")
    assert single.valid
    assert [stop.agency_key for stop in single.stops] == ["gedimat:20"]


def test_two_gedimat_stores_are_two_stops():
    products = {
        1: ProductRead(id=1, code="PMC-A", name="A", category="VIS", reference_unit="pièce", description=None),
        2: ProductRead(id=2, code="PMC-B", name="B", category="VIS", reference_unit="pièce", description=None),
    }
    offers = [
        _offer(1, 10, "Magasin A", 49.05, "4.00", "available", 5),
        _offer(2, 20, "Magasin B", 49.40, "6.00", "available", 5),
    ]
    service = ComparisonService([_FixedConnector(offers)], 49.0, 2.0, origin=_origin())
    result = service.compare(
        [
            CartLine(product_id=1, quantity=Decimal("1")),
            CartLine(product_id=2, quantity=Decimal("1")),
        ],
        products,
    )
    compromise = next(item for item in result.strategies if item.key == "best_compromise")
    assert compromise.valid
    assert {stop.agency_key for stop in compromise.stops} == {"gedimat:10", "gedimat:20"}
    assert len(compromise.stops) == 2
