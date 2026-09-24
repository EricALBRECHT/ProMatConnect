"""Cache-busting des assets JS/CSS via APP_VERSION centralisée."""

from pathlib import Path

from app.version import APP_VERSION

STATIC_ASSETS = ("styles.css", "materials.js", "app.js", "chantiers.js")


def test_app_version_is_stable_semver():
    assert APP_VERSION
    assert "?" not in APP_VERSION
    parts = APP_VERSION.split(".")
    assert len(parts) >= 2
    assert all(part.isdigit() for part in parts)


def test_openapi_exposes_same_app_version(client):
    assert client.get("/openapi.json").json()["info"]["version"] == APP_VERSION


def test_templates_use_static_asset_helper_not_raw_url_for():
    templates = Path("app/templates")
    for path in templates.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert "url_for('static'" not in text, path
        assert "asset_v" not in text, path


def test_pages_reference_versioned_static_urls(client):
    home = client.get("/").text
    assert f'/static/styles.css?v={APP_VERSION}' in home
    assert f'/static/materials.js?v={APP_VERSION}' in home
    assert f'/static/app.js?v={APP_VERSION}' in home
    assert "url_for('static'" not in home

    listing = client.get("/chantiers").text
    assert f'/static/materials.js?v={APP_VERSION}' in listing
    assert f'/static/chantiers.js?v={APP_VERSION}' in listing

    nouveau = client.get("/chantiers/nouveau").text
    assert f'/static/chantiers.js?v={APP_VERSION}' in nouveau

    created = client.post(
        "/api/chantiers",
        json={
            "nom": "Cache bust détail",
            "adresse": "10 rue du Chantier, 75004 Paris",
            "materiaux": [{"product_id": 1, "quantite": "1", "ordre": 0}],
        },
    ).json()
    detail = client.get(f"/chantiers/{created['id']}").text
    assert f'/static/materials.js?v={APP_VERSION}' in detail
    assert f'/static/chantiers.js?v={APP_VERSION}' in detail
    assert 'id="compare-prices"' in detail
    assert 'id="delete-chantier"' in detail


def test_static_files_still_served_without_and_with_query(client):
    for name in STATIC_ASSETS:
        bare = client.get(f"/static/{name}")
        assert bare.status_code == 200, name
        versioned = client.get(f"/static/{name}", params={"v": APP_VERSION})
        assert versioned.status_code == 200, name
        assert versioned.content == bare.content
