"""Tests offline — client Géoplateforme IGN (transport mocké)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import Settings
from app.schemas.location import Coordinates, OriginRequest
from app.services.geocoding import (
    AddressNotFound,
    FakeGeocodingService,
    GeocodingUnavailable,
    GeopfGeocodingService,
    build_geocoding_service,
    select_best_feature,
)
from app.services.origin import OriginService

FIXTURES = Path(__file__).parent / "fixtures" / "geopf"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class RecordingTransport:
    def __init__(self, responses: list[tuple[int, bytes] | Exception]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, *, url: str, timeout: float) -> tuple[int, bytes]:
        self.calls.append({"url": url, "timeout": timeout})
        if not self.responses:
            raise AssertionError("plus de réponses mock")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _json_response(name: str, status: int = 200) -> tuple[int, bytes]:
    return status, json.dumps(_load(name)).encode("utf-8")


def test_search_address_returns_city_postcode_citycode():
    transport = RecordingTransport([_json_response("search_amiens.json")])
    service = GeopfGeocodingService(transport=transport)
    result = service.geocode("1 Place Jean Catelas, Amiens")
    assert result.latitude == pytest.approx(49.894)
    assert result.longitude == pytest.approx(2.299)
    assert result.city == "Amiens"
    assert result.postcode == "80000"
    assert result.citycode == "80021"
    assert result.score == pytest.approx(0.91)
    assert result.source == "geopf"
    assert "q=" in transport.calls[0]["url"]
    assert "geocodage/search" in transport.calls[0]["url"]


def test_select_best_by_score_not_first_feature():
    result = select_best_feature(_load("reordered_scores.json"))
    assert "Meilleur score" in (result.label or "")
    assert result.score == pytest.approx(0.97)


def test_reverse_returns_city_postcode_citycode():
    transport = RecordingTransport([_json_response("reverse_amiens.json")])
    service = GeopfGeocodingService(transport=transport)
    result = service.reverse(49.8941, 2.2957)
    assert result.city == "Amiens"
    assert result.postcode == "80000"
    assert result.citycode == "80021"
    assert "lon=" in transport.calls[0]["url"]
    assert "lat=" in transport.calls[0]["url"]
    assert "geocodage/reverse" in transport.calls[0]["url"]


def test_unknown_address_empty_features():
    transport = RecordingTransport([_json_response("empty_features.json")])
    service = GeopfGeocodingService(transport=transport)
    with pytest.raises(AddressNotFound, match="Aucun résultat"):
        service.geocode("Adresse totalement inventée XYZ")


def test_feature_without_citycode():
    transport = RecordingTransport([_json_response("missing_citycode.json")])
    service = GeopfGeocodingService(transport=transport)
    with pytest.raises(AddressNotFound, match="citycode"):
        service.geocode("Quelque part")


def test_http_429():
    transport = RecordingTransport([(429, b"rate limit")])
    service = GeopfGeocodingService(transport=transport)
    with pytest.raises(GeocodingUnavailable, match="429"):
        service.geocode("Amiens")


def test_http_500():
    transport = RecordingTransport([(500, b"boom")])
    service = GeopfGeocodingService(transport=transport)
    with pytest.raises(GeocodingUnavailable, match="500"):
        service.reverse(49.89, 2.29)


def test_timeout():
    transport = RecordingTransport([TimeoutError("timed out")])
    service = GeopfGeocodingService(transport=transport, timeout=3.0)
    with pytest.raises(GeocodingUnavailable, match="Délai"):
        service.geocode("Amiens")


def test_invalid_json():
    transport = RecordingTransport([(200, b"not-json{{{")])
    service = GeopfGeocodingService(transport=transport)
    with pytest.raises(GeocodingUnavailable, match="JSON"):
        service.geocode("Amiens")


def test_empty_address():
    service = GeopfGeocodingService(transport=RecordingTransport([]))
    with pytest.raises(AddressNotFound, match="vide"):
        service.geocode("   ")


def test_invalid_coordinates():
    service = GeopfGeocodingService(transport=RecordingTransport([]))
    with pytest.raises(AddressNotFound, match="bornes|invalides"):
        service.reverse(91.0, 2.0)
    with pytest.raises(AddressNotFound, match="bornes|invalides"):
        service.reverse(49.0, 200.0)


def test_origin_service_site_enriched_with_geopf():
    transport = RecordingTransport([_json_response("search_amiens.json")])
    geocoder = GeopfGeocodingService(transport=transport)
    origin = OriginService(Settings(), geocoder).resolve(
        OriginRequest(type="site", address="1 Place Jean Catelas, Amiens")
    )
    assert origin.citycode == "80021"
    assert origin.city == "Amiens"
    assert origin.postcode == "80000"
    assert origin.source == "geopf"
    assert origin.latitude == pytest.approx(49.894)


def test_origin_service_current_location_reverse_soft_enrich():
    transport = RecordingTransport([_json_response("reverse_amiens.json")])
    geocoder = GeopfGeocodingService(transport=transport)
    origin = OriginService(Settings(), geocoder).resolve(
        OriginRequest(type="current_location", latitude=49.8941, longitude=2.2957)
    )
    assert origin.citycode == "80021"
    assert origin.latitude == pytest.approx(49.8941)
    assert origin.longitude == pytest.approx(2.2957)
    assert origin.source == "geopf"


def test_origin_service_coords_soft_fail_keeps_routing_coords():
    transport = RecordingTransport([(500, b"err")])
    geocoder = GeopfGeocodingService(transport=transport)
    origin = OriginService(Settings(), geocoder).resolve(
        OriginRequest(type="site", latitude=49.9, longitude=2.3)
    )
    assert origin.latitude == 49.9 and origin.longitude == 2.3
    assert origin.citycode is None
    assert origin.source == "coordinates"


def test_origin_company_with_fake_keeps_configuration_source():
    settings = Settings(company_address="Mon entreprise", company_latitude=49, company_longitude=3)
    origin = OriginService(settings, FakeGeocodingService()).resolve(OriginRequest(type="company"))
    assert origin.source == "configuration"
    assert origin.citycode is None


def test_build_geocoding_service_factory():
    assert isinstance(build_geocoding_service("fake"), FakeGeocodingService)
    assert isinstance(build_geocoding_service("geopf"), GeopfGeocodingService)


def test_fake_geocode_still_returns_compatible_result():
    result = FakeGeocodingService().geocode("10 rue du Chantier, 75004 Paris")
    assert result.latitude == 48.8566
    assert result.citycode is None
    assert result.source == "fake_geocoding"


def test_alternative_coordinates_duck_typing():
    class Alt(FakeGeocodingService):
        provider = "alt"

        def geocode(self, address):
            return Coordinates(latitude=48.1, longitude=2.1)

    origin = OriginService(Settings(), Alt()).resolve(
        OriginRequest(type="other", address="x" * 5)
    )
    assert origin.latitude == 48.1
    assert origin.source == "alt"
