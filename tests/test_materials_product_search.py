"""Recherche matériau Chantier / Comparateur : le serveur est interrogé, page plafonnée."""

from pathlib import Path


def test_material_search_asks_the_server_within_the_page_limit(client):
    materials = Path("app/static/materials.js").read_text(encoding="utf-8")
    assert "function productSearchUrl" in materials
    assert 'params.set("limit", String(PRODUCT_PAGE_LIMIT))' in materials
    assert "PRODUCT_PAGE_LIMIT = 100" in materials
    assert 'params.set("q", q)' in materials
    assert "SEARCH_DEBOUNCE_MS = 250" in materials
    assert "searchOnServer" in materials
    assert 'fetch("/api/products")' not in materials
    assert "bindMaterialLines" in Path("app/static/app.js").read_text(encoding="utf-8")
    assert "bindMaterialLines" in Path("app/static/chantiers.js").read_text(encoding="utf-8")

    first = client.get("/api/products", params={"limit": 100}).json()
    assert len(first) <= 100
    first_codes = {item["code"] for item in first}

    early = client.get("/api/products", params={"q": "BA13", "limit": 100}).json()
    assert early
    assert len(early) <= 100
    assert any(item["code"] in first_codes for item in early)

    agglo = client.get("/api/products", params={"q": "agglo", "limit": 100}).json()
    assert agglo
    assert len(agglo) <= 100
    assert any(item["code"].startswith("PMC-VIS-AGGLO-") for item in agglo)
    assert all(item["is_active"] for item in agglo)
