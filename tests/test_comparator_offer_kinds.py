"""Distinction structurelle : catalogue national ≠ magasin sans coords ≠ géoloc."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.connectors.base import AgencyData, ConnectorOffer
from app.schemas.catalog import ProductRead
from app.schemas.comparison import CartLine
from app.services.comparison import ComparisonService
from app.version import APP_VERSION


class _Stub:
    def __init__(self, name, offers):
        self.name, self.offers = name, offers

    @property
    def supplier_name(self):
        return self.name

    def get_offers(self, product_ids):
        return [o for o in self.offers if o.product_id in product_ids]


def _product():
    return {
        1: ProductRead(
            id=1,
            code="PMC-BA13-STD-2500X1200",
            name="Plaque BA13 standard",
            category="Plaques",
            reference_unit="pièce",
            description=None,
        )
    }


def _offer(*, agency: AgencyData, price: str = "8.37") -> ConnectorOffer:
    return ConnectorOffer(
        supplier="LEROY_MERLIN",
        product_id=1,
        price=Decimal(price),
        stock=0 if not agency.is_geolocated else 50,
        reference_quantity=Decimal("1"),
        supplier_reference="70505960",
        supplier_unit="plaque",
        reference_unit="pièce",
        packaging_quantity=Decimal("1"),
        preparation_minutes=60,
        updated_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        agency=agency,
        tax_basis="TTC",
    )


def _national_agency() -> AgencyData:
    return AgencyData(
        id=10,
        name="LEROY_MERLIN (catalogue national)",
        address="",
        postal_code="",
        city="",
        latitude=None,
        longitude=None,
        external_id=None,
    )


def _ungeolocated_store() -> AgencyData:
    return AgencyData(
        id=20,
        name="Leroy Merlin Ivry",
        address="12 rue du Commerce",
        postal_code="94200",
        city="Ivry-sur-Seine",
        latitude=None,
        longitude=None,
        external_id="STORE-IVRY",
    )


def _geolocated_store() -> AgencyData:
    return AgencyData(
        id=30,
        name="Leroy Merlin Ivry",
        address="12 rue du Commerce",
        postal_code="94200",
        city="Ivry-sur-Seine",
        latitude=48.8156,
        longitude=2.3845,
        external_id="STORE-IVRY",
    )


def test_agency_data_national_vs_store_structural():
    national = _national_agency()
    assert national.is_geolocated is False
    assert national.is_national_catalog is True

    store = _ungeolocated_store()
    assert store.is_geolocated is False
    assert store.is_national_catalog is False

    geo = _geolocated_store()
    assert geo.is_geolocated is True
    assert geo.is_national_catalog is False

    # Ne pas déduire du libellé « catalogue national » si identité magasin présente
    fake_label = AgencyData(
        id=99,
        name="FOURNISSEUR (catalogue national)",
        address="1 rue Magasin",
        postal_code="75001",
        city="Paris",
        latitude=None,
        longitude=None,
        external_id="S1",
    )
    assert fake_label.is_national_catalog is False


def test_national_catalog_stop_flags_no_distance_no_route():
    service = ComparisonService(
        [_Stub("LEROY_MERLIN", [_offer(agency=_national_agency())])],
        48.8566,
        2.3522,
        tax_basis="TTC",
    )
    result = service.compare([CartLine(product_id=1, quantity=Decimal("1"))], _product())
    minimum = next(s for s in result.strategies if s.key == "minimum_materials")
    assert minimum.valid is True
    assert len(minimum.stops) == 1
    stop = minimum.stops[0]
    assert stop.is_national_catalog is True
    assert stop.is_geolocated is False
    assert stop.distance_km is None
    assert minimum.total_distance_km is None
    assert minimum.travel_minutes is None
    assert minimum.route is None


def test_ungeolocated_store_not_national():
    service = ComparisonService(
        [_Stub("LEROY_MERLIN", [_offer(agency=_ungeolocated_store(), price="9.00")])],
        48.8566,
        2.3522,
        tax_basis="TTC",
    )
    result = service.compare([CartLine(product_id=1, quantity=Decimal("1"))], _product())
    minimum = next(s for s in result.strategies if s.key == "minimum_materials")
    stop = minimum.stops[0]
    assert stop.is_national_catalog is False
    assert stop.is_geolocated is False
    assert stop.distance_km is None
    assert stop.name == "Leroy Merlin Ivry"
    assert minimum.route is None
    assert minimum.total_distance_km is None


def test_geolocated_store_keeps_distance_behavior():
    service = ComparisonService(
        [_Stub("LEROY_MERLIN", [_offer(agency=_geolocated_store(), price="9.50")])],
        48.8566,
        2.3522,
        tax_basis="TTC",
    )
    result = service.compare([CartLine(product_id=1, quantity=Decimal("1"))], _product())
    minimum = next(s for s in result.strategies if s.key == "minimum_materials")
    stop = minimum.stops[0]
    assert stop.is_national_catalog is False
    assert stop.is_geolocated is True
    assert stop.distance_km is not None
    assert stop.distance_km > 0


def test_ui_national_vs_ungeolocated_copy():
    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "Aucun magasin associé" in app_js
    assert "Catalogue national" in app_js
    assert "Non géolocalisée" in app_js
    assert "Non calculable" in app_js
    assert (
        "Prix catalogue uniquement. La disponibilité et le prix dans un "
        "magasin précis ne sont pas encore connus."
    ) in app_js
    # Ancien libellé trompeur pour le cas national
    assert '["Point de vente", "non géolocalisé"]' not in app_js
    assert "is_national_catalog" in app_js
    assert APP_VERSION == "0.9.3"
