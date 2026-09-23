import pytest
from pydantic import ValidationError

from app.config import Settings
from app.schemas.location import OriginRequest
from app.services.geocoding import AddressNotFound, FakeGeocodingService
from app.services.origin import OriginService


def test_fake_geocoding_deterministic_and_normalized():
    service = FakeGeocodingService()
    first = service.geocode("10 rue du Chantier, 75004 Paris")
    assert first.latitude == 48.8566
    assert first == service.geocode(" 10 RUE DU CHANTIER 75004 PARIS ")
    assert service.geocode("8 rue du Dépôt, 93200 Saint-Denis").latitude == 48.925


def test_unknown_address_is_not_silently_replaced():
    with pytest.raises(AddressNotFound):
        FakeGeocodingService().geocode("Une adresse inconnue")


def test_site_origin():
    origin = OriginService(Settings(), FakeGeocodingService()).resolve(
        OriginRequest(
            type="site",
            address="10 rue du Chantier, 75004 Paris",
        )
    )
    assert origin.type == "site" and origin.label == "Chantier"
    assert origin.latitude == 48.8566 and origin.source == "fake_geocoding"


def test_authorized_user_coordinates():
    origin = OriginService(Settings(), FakeGeocodingService()).resolve(
        OriginRequest(
            type="current_location",
            latitude=50.1,
            longitude=2.4,
        )
    )
    assert origin.source == "coordinates" and origin.latitude == 50.1


def test_company_uses_local_configuration():
    settings = Settings(company_address="Mon entreprise", company_latitude=49, company_longitude=3)
    origin = OriginService(settings, FakeGeocodingService()).resolve(OriginRequest(type="company"))
    assert origin.address == "Mon entreprise" and origin.latitude == 49
    assert origin.source == "configuration"


def test_other_address():
    origin = OriginService(Settings(), FakeGeocodingService()).resolve(
        OriginRequest(
            type="other",
            address="5 rue des Artisans, 94200 Ivry-sur-Seine",
        )
    )
    assert origin.type == "other" and origin.latitude == 48.812


def test_legacy_origin_fallback_configured():
    origin = OriginService(
        Settings(user_latitude=42, user_longitude=1), FakeGeocodingService()
    ).resolve(None)
    assert origin.source == "configuration" and origin.latitude == 42


@pytest.mark.parametrize(
    "data",
    [
        {"type": "current_location"},
        {"type": "site"},
        {"type": "other", "address": "  "},
        {"type": "site", "latitude": 48},
        {"type": "site", "latitude": 91, "longitude": 0},
        {"type": "current_location", "latitude": "NaN", "longitude": 0},
        {"type": "site", "address": "Paris", "latitude": 48, "longitude": 2},
        {"type": "company", "address": "Paris"},
        {"type": "express"},
        {"type": "current_location", "latitude": 48, "longitude": 181},
    ],
)
def test_invalid_origins(data):
    with pytest.raises(ValidationError):
        OriginRequest(**data)
