"""Identité agency_key — anti-collision DB PK vs entity_id LIVE."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.connectors.base import AgencyData, ConnectorOffer, db_agency_key, live_agency_key
from app.connectors.bricodepot.connector import AGENCY_NAMESPACE, retailer_to_agency_data
from app.connectors.bricodepot.client import BricoRetailer
from app.schemas.catalog import ProductRead
from app.schemas.comparison import CartLine
from app.schemas.location import ResolvedOrigin
from app.services.comparison import ComparisonService
from app.services.optimization import ProcurementOptimizer
from app.services.procurement_cost import CostParameters, ProcurementCostService
from app.services.routing import ExactRouteOrderOptimizer, FakeRoutingService
from app.services.shopping_list import (
    build_shopping_list_from_snapshot,
    make_line_key,
    parse_line_key,
)


def _product(pid: int = 5) -> ProductRead:
    return ProductRead(
        id=pid,
        code=f"PMC{pid:04d}",
        name=f"Produit {pid}",
        category="Test",
        reference_unit="piece",
        description=None,
    )


def _offer(
    *,
    supplier: str,
    agency: AgencyData,
    product_id: int = 5,
    price: str = "10.00",
    stock: int = 20,
) -> ConnectorOffer:
    return ConnectorOffer(
        supplier=supplier,
        agency=agency,
        product_id=product_id,
        supplier_reference=f"{supplier}-{product_id}",
        supplier_unit="piece",
        reference_quantity=Decimal("1"),
        packaging_quantity=Decimal("1"),
        price=Decimal(price),
        tax_basis="HT",
        vat_rate=Decimal("20"),
        stock=stock,
        preparation_minutes=15,
        updated_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
    )


class StubConnector:
    def __init__(self, name: str, offers: list[ConnectorOffer]):
        self._name = name
        self._offers = offers

    @property
    def supplier_name(self) -> str:
        return self._name

    def get_offers(self, product_ids):
        return [o for o in self._offers if o.product_id in product_ids]


def test_db_and_live_agency_key_formats():
    assert db_agency_key(10) == "db:10"
    assert live_agency_key("bricodepot", 10) == "bricodepot:10"
    with pytest.raises(ValueError):
        live_agency_key("api:BRICO_DEPOT", 10)


def test_agency_data_defaults_to_db_key():
    agency = AgencyData(
        id=10,
        name="Agence DB",
        address="1 rue",
        postal_code="75001",
        city="Paris",
        latitude=48.85,
        longitude=2.35,
    )
    assert agency.agency_key == "db:10"


def test_bricodepot_retailer_agency_key():
    agency = retailer_to_agency_data(
        BricoRetailer(
            entity_id=10,
            seller_code="2350",
            name="AMIENS",
            address="x",
            city="Amiens",
            postcode="80000",
            latitude=49.89,
            longitude=2.29,
            phone=None,
            distance_km=1.0,
        )
    )
    assert agency.id == 10
    assert agency.agency_key == f"{AGENCY_NAMESPACE}:10"
    assert agency.external_id == "2350"


def test_line_key_legacy_and_live():
    assert make_line_key(10, 5) == "10:5"
    assert make_line_key(10, 5, agency_key="db:10") == "10:5"
    assert make_line_key(10, 5, agency_key="bricodepot:10") == "bricodepot:10:5"

    legacy = parse_line_key("10:5")
    assert legacy.agency_key == "db:10"
    assert legacy.agency_id == 10
    assert legacy.product_id == 5

    live = parse_line_key("bricodepot:10:5")
    assert live.agency_key == "bricodepot:10"
    assert live.agency_id == 10
    assert live.product_id == 5


def test_comparison_keeps_db_and_live_id_10_distinct():
    db_agency = AgencyData(
        id=10,
        name="Point P Paris",
        address="a",
        postal_code="75001",
        city="Paris",
        latitude=48.8566,
        longitude=2.3522,
        agency_key="db:10",
    )
    live_agency = AgencyData(
        id=10,
        name="Brico Amiens",
        address="b",
        postal_code="80000",
        city="Amiens",
        latitude=49.894,
        longitude=2.295,
        agency_key="bricodepot:10",
    )
    offers = [
        _offer(supplier="POINT.P", agency=db_agency, price="9.00"),
        _offer(supplier="BRICO DEPOT", agency=live_agency, price="8.00"),
    ]
    service = ComparisonService(
        [
            StubConnector("POINT.P", [offers[0]]),
            StubConnector("BRICO DEPOT", [offers[1]]),
        ],
        48.8566,
        2.3522,
    )
    # Option mono-fournisseur POINT.P
    result = service.compare([CartLine(product_id=5, quantity=Decimal("1"))], {5: _product()})
    keys = {
        a.agency_key
        for opt in result.options
        for a in opt.agencies
    }
    assert "db:10" in keys
    assert "bricodepot:10" in keys
    # Optimisé : peut contenir les deux stops
    optimized = next(o for o in result.options if o.key == "optimized")
    # L'offre la moins chère gagne pour le produit : BRICO 8.00
    assert optimized.available[0].agency_key == "bricodepot:10"
    assert optimized.available[0].agency_id == 10


def test_optimizer_two_stops_same_numeric_id():
    db_agency = AgencyData(
        id=10,
        name="DB Store",
        address="a",
        postal_code="75001",
        city="Paris",
        latitude=48.85,
        longitude=2.35,
        agency_key="db:10",
    )
    live_agency = AgencyData(
        id=10,
        name="Live Store",
        address="b",
        postal_code="80000",
        city="Amiens",
        latitude=49.89,
        longitude=2.30,
        agency_key="bricodepot:10",
    )
    # Deux produits : chacun disponible dans une seule agence → 2 stops obligatoires
    offers = [
        _offer(supplier="A", agency=db_agency, product_id=1, price="5.00"),
        _offer(supplier="B", agency=live_agency, product_id=2, price="6.00"),
    ]

    def factory(subset):
        return ComparisonService(
            [StubConnector("mix", subset)], 48.85, 2.35
        )._option("x", "x", [
            CartLine(product_id=1, quantity=Decimal("1")),
            CartLine(product_id=2, quantity=Decimal("1")),
        ], {1: _product(1), 2: _product(2)}, subset, "HT")

    strategies, _ = ProcurementOptimizer(
        FakeRoutingService(),
        ExactRouteOrderOptimizer(),
        ProcurementCostService(CostParameters()),
        max_agencies=8,
    ).optimize(
        [
            CartLine(product_id=1, quantity=Decimal("1")),
            CartLine(product_id=2, quantity=Decimal("1")),
        ],
        offers,
        ResolvedOrigin(
            type="site",
            label="O",
            latitude=48.85,
            longitude=2.35,
            source="test",
        ),
        factory,
    )
    # Au moins une stratégie valide avec 2 stops distincts
    valid = [s for s in strategies if s.valid and len(s.stops) == 2]
    assert valid
    keys = {s.agency_key for s in valid[0].stops}
    assert keys == {"db:10", "bricodepot:10"}
    assert len({s.id for s in valid[0].stops}) == 1  # même id technique 10
    route_keys = {p.agency_key for p in valid[0].route.points if p.agency_key}
    assert route_keys == {"db:10", "bricodepot:10"}


def test_shopping_list_distinct_stores_and_line_keys():
    chantier = SimpleNamespace(
        id=1, nom="C", client=None, adresse="x", date_prevue=None
    )
    appro = SimpleNamespace(
        id=7,
        strategy_key="best_compromise",
        strategy_title="Test",
        chosen_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
        material_total=Decimal("20.00"),
        estimated_procurement_cost=None,
        total_distance_km=None,
        travel_minutes=None,
        tax_basis="HT",
        currency="EUR",
        obsolete=False,
        snapshot={
            "strategy": {
                "key": "best_compromise",
                "title": "Test",
                "stops": [
                    {
                        "id": 10,
                        "agency_key": "db:10",
                        "supplier": "POINT.P",
                        "name": "DB",
                        "address": "a",
                        "postal_code": "75001",
                        "city": "Paris",
                    },
                    {
                        "id": 10,
                        "agency_key": "bricodepot:10",
                        "supplier": "BRICO DEPOT",
                        "name": "Amiens",
                        "address": "b",
                        "postal_code": "80000",
                        "city": "Amiens",
                    },
                ],
                "lines": [
                    {
                        "product_id": 5,
                        "agency_id": 10,
                        "agency_key": "db:10",
                        "product_name": "P",
                        "supplier": "POINT.P",
                        "supplier_reference": "A",
                        "reference_unit": "piece",
                        "supplier_unit": "piece",
                        "requested_quantity": "1",
                        "purchased_quantity": "1",
                        "packs": 1,
                        "pack_price": "10.00",
                        "line_total": "10.00",
                        "reference_quantity": "1",
                        "packaging_quantity": "1",
                    },
                    {
                        "product_id": 5,
                        "agency_id": 10,
                        "agency_key": "bricodepot:10",
                        "product_name": "P",
                        "supplier": "BRICO DEPOT",
                        "supplier_reference": "B",
                        "reference_unit": "piece",
                        "supplier_unit": "piece",
                        "requested_quantity": "1",
                        "purchased_quantity": "1",
                        "packs": 1,
                        "pack_price": "9.00",
                        "line_total": "9.00",
                        "reference_quantity": "1",
                        "packaging_quantity": "1",
                    },
                ],
                "route": None,
                "cost_breakdown": {},
            }
        },
    )
    data = build_shopping_list_from_snapshot(chantier, appro)
    assert len(data.stores) == 2
    keys = {s.agency_key for s in data.stores}
    assert keys == {"db:10", "bricodepot:10"}
    line_keys = {ln.line_key for st in data.stores for ln in st.lines}
    assert line_keys == {"10:5", "bricodepot:10:5"}


def test_legacy_snapshot_without_agency_key_still_works():
    chantier = SimpleNamespace(
        id=1, nom="C", client=None, adresse="x", date_prevue=None
    )
    appro = SimpleNamespace(
        id=3,
        strategy_key="single_stop",
        strategy_title="1 seul arrêt",
        chosen_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
        material_total=Decimal("10.00"),
        estimated_procurement_cost=None,
        total_distance_km=None,
        travel_minutes=None,
        tax_basis="HT",
        currency="EUR",
        obsolete=False,
        snapshot={
            "strategy": {
                "stops": [
                    {
                        "id": 10,
                        "supplier": "POINT.P",
                        "name": "Agence",
                        "address": "a",
                        "postal_code": "75001",
                        "city": "Paris",
                    }
                ],
                "lines": [
                    {
                        "product_id": 5,
                        "agency_id": 10,
                        "product_name": "P",
                        "supplier": "POINT.P",
                        "supplier_reference": "A",
                        "reference_unit": "piece",
                        "supplier_unit": "piece",
                        "requested_quantity": "1",
                        "purchased_quantity": "1",
                        "packs": 1,
                        "pack_price": "10.00",
                        "line_total": "10.00",
                    }
                ],
                "route": None,
                "cost_breakdown": {},
            }
        },
    )
    data = build_shopping_list_from_snapshot(chantier, appro)
    assert len(data.stores) == 1
    assert data.stores[0].agency_key == "db:10"
    assert data.stores[0].lines[0].line_key == "10:5"
    # Suivi historique : clé legacy toujours parseable
    parsed = parse_line_key("10:5")
    assert parsed.agency_key == "db:10"
