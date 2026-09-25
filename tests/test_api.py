from decimal import Decimal

import pytest


def test_catalog_api(client):
    from scripts.normalized_catalog import NORMALIZED_PRODUCT_COUNT

    products = client.get("/api/products").json()
    assert len(products) == 20 + NORMALIZED_PRODUCT_COUNT
    mapping = client.get("/api/products", params={"q": "ba13", "for_mapping": True}).json()
    assert mapping
    assert mapping[0]["code"].startswith("PMC-BA13")
    assert all(not p.get("is_legacy") for p in mapping)
    legacy = client.get("/api/products", params={"q": "PMC0001"}).json()
    assert legacy[0]["code"] == "PMC0001"
    assert client.get(f"/api/products/{products[0]['id']}").json()["id"] == products[0]["id"]
    assert client.get("/api/products/9999").status_code == 404


def test_compare_api(client):
    response = client.post(
        "/api/compare",
        json={
            "lines": [
                {"product_id": 1, "quantity": "30"},
                {"product_id": 2, "quantity": "10"},
                {"product_id": 3, "quantity": "20"},
            ]
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["simulated"] and data["tax_basis"] == "HT"
    assert len(data["options"]) == 3
    assert all(o["valid"] for o in data["options"])
    totals = [Decimal(o["total"]) for o in data["options"]]
    assert totals[-1] <= min(totals[:2])
    assert isinstance(data["options"][0]["total"], str)


def test_api_unavailable(client):
    data = client.post("/api/compare", json={"lines": [{"product_id": 20, "quantity": "1"}]}).json()
    assert all(not o["valid"] and o["total"] is None for o in data["options"])


def test_api_supplier_specific_stock(client):
    data = client.post("/api/compare", json={"lines": [{"product_id": 18, "quantity": "1"}]}).json()
    assert [o["valid"] for o in data["options"]] == [False, True, True]


def test_unknown_product(client):
    assert (
        client.post(
            "/api/compare", json={"lines": [{"product_id": 9999, "quantity": "1"}]}
        ).status_code
        == 404
    )


@pytest.mark.parametrize("quantity", ["0", "-1", "1000001", "0.0001", "NaN", "Infinity", "abc"])
def test_invalid_quantity(client, quantity):
    assert (
        client.post(
            "/api/compare", json={"lines": [{"product_id": 1, "quantity": quantity}]}
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"lines": []},
        {"lines": [{"product_id": 1, "quantity": "1"}] * 2},
        {"lines": [{"product_id": 1, "quantity": "1"}], "latitude": 500},
        {"lines": [{"product_id": 0, "quantity": "1"}]},
        {"lines": [{"product_id": i + 1, "quantity": "1"} for i in range(101)]},
    ],
)
def test_invalid_cart(client, payload):
    assert client.post("/api/compare", json=payload).status_code == 422


def test_home_assets_health_swagger(client):
    assert "Préparez votre liste" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/docs").status_code == 200
    assert "/api/compare" in client.get("/openapi.json").json()["paths"]


def test_api_database_failure(client, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from app.repositories.catalog import CatalogRepository

    def fail(*args, **kwargs):
        raise OperationalError("secret SQL", {}, Exception("sensitive information"))

    monkeypatch.setattr(CatalogRepository, "list", fail)
    response = client.get("/api/products")
    assert response.status_code == 503
    assert "secret" not in response.text and "sensitive" not in response.text
