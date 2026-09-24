import pytest

from app.routes.api import get_geocoding_service, get_routing_service
from app.schemas.location import Coordinates
from app.services.geocoding import GeocodingService
from app.services.routing import FakeRoutingService

LINES = [
    {"product_id": 1, "quantity": "30"},
    {"product_id": 2, "quantity": "10"},
    {"product_id": 3, "quantity": "20"},
]


@pytest.mark.parametrize(
    "origin",
    [
        {"type": "site", "address": "10 rue du Chantier, 75004 Paris"},
        {"type": "current_location", "latitude": 48.8566, "longitude": 2.3522},
        {"type": "company"},
        {"type": "other", "address": "5 rue des Artisans, 94200 Ivry-sur-Seine"},
        {"type": "site", "latitude": 48.8566, "longitude": 2.3522},
    ],
)
def test_api_origin_and_new_contract(client, origin):
    response = client.post("/api/compare", json={"lines": LINES, "origin": origin})
    assert response.status_code == 200
    data = response.json()
    assert data["origin"]["type"] == origin["type"]
    assert [s["key"] for s in data["strategies"]] == [
        "single_stop",
        "minimum_materials",
        "best_compromise",
    ]
    assert all(s["valid"] for s in data["strategies"])
    for strategy in data["strategies"]:
        assert isinstance(strategy["material_total"], str)
        assert isinstance(strategy["estimated_procurement_cost"], str)
        assert strategy["route"]["points"][0]["key"] == "origin"
        assert strategy["route"]["points"][-1]["key"] == "origin"
        assert len(strategy["stops"]) == len(strategy["route"]["legs"]) - 1
        assert len(strategy["lines"]) == 3
    assert len(data["strategies"][0]["stops"]) == 1
    assert len(data["options"]) == 3  # Contrat historique conservé.


@pytest.mark.parametrize(
    "origin",
    [
        {"type": "other", "address": "Adresse inexistante"},
        {"type": "current_location"},
        {"type": "current_location", "latitude": 50},
        {"type": "current_location", "latitude": 100, "longitude": 1},
        {"type": "site", "latitude": "Infinity", "longitude": 1},
        {"type": "company", "latitude": 48, "longitude": 2},
        {"type": "current_location", "address": "Paris"},
    ],
)
def test_api_rejects_invalid_origin(client, origin):
    assert client.post("/api/compare", json={"lines": LINES, "origin": origin}).status_code == 422


def test_api_unknown_address_explained(client):
    response = client.post(
        "/api/compare", json={"lines": LINES, "origin": {"type": "site", "address": "Inconnu"}}
    )
    assert "Adresse inconnue" in response.json()["detail"]


def test_api_unavailable_v02(client):
    data = client.post("/api/compare", json={"lines": [{"product_id": 20, "quantity": "1"}]}).json()
    assert all(
        not s["valid"] and s["material_total"] is None and s["estimated_procurement_cost"] is None
        for s in data["strategies"]
    )


def test_new_origin_changes_routes(client):
    near = client.post(
        "/api/compare",
        json={
            "lines": LINES,
            "origin": {"type": "current_location", "latitude": 48.85, "longitude": 2.4},
        },
    ).json()
    far = client.post(
        "/api/compare",
        json={
            "lines": LINES,
            "origin": {"type": "current_location", "latitude": 49.8, "longitude": 2.4},
        },
    ).json()
    assert near["strategies"][1]["travel_minutes"] != far["strategies"][1]["travel_minutes"]


def test_geocoding_and_routing_can_be_replaced_without_changing_engine(client):
    class AlternativeGeocoder(GeocodingService):
        provider = "test_alternative"

        def geocode(self, address):
            return Coordinates(latitude=48.8566, longitude=2.3522)

    class AlternativeRouter(FakeRoutingService):
        provider = "test_router"

        def leg(self, start, end):
            return (
                super()
                .leg(start, end)
                .model_copy(update={"distance_km": 1.0, "duration_minutes": 2.0})
            )

    client.app.dependency_overrides[get_geocoding_service] = lambda: AlternativeGeocoder()
    client.app.dependency_overrides[get_routing_service] = lambda: AlternativeRouter()
    data = client.post(
        "/api/compare",
        json={"lines": LINES, "origin": {"type": "other", "address": "Toute adresse"}},
    ).json()
    assert data["origin"]["source"] == "test_alternative"
    result = data["strategies"][0]
    assert result["route"]["provider"] == "test_router"
    assert result["travel_minutes"] == "4.0" and result["total_distance_km"] == "2.0"


def test_swagger_v02(client):
    schema = client.get("/openapi.json").json()
    assert schema["info"]["version"] == "0.2.0"
    assert "origin" in schema["components"]["schemas"]["CompareRequest"]["properties"]
    schemas = schema["components"]["schemas"]
    strategy = (
        schemas.get("ProcurementStrategy")
        or schemas.get("ProcurementStrategy-Output")
        or schemas.get("ProcurementStrategy-Input")
    )
    assert strategy is not None
    assert "estimated_procurement_cost" in strategy["properties"]
