"""UI comparateur : géocodage fake vs geopf exposé par le serveur."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.version import APP_VERSION


def _home(client: TestClient) -> str:
    response = client.get("/")
    assert response.status_code == 200
    return response.text


def test_fake_mode_keeps_demo_geocoding_ui(client):
    html = _home(client)
    assert 'data-geocoding-provider="fake"' in html
    assert 'data-bricodepot-live-enabled="false"' in html
    assert "Géocodage simulé" in html
    assert 'list="demo-addresses"' in html
    assert 'id="demo-addresses"' in html
    assert "Mode démonstration : fournisseurs, agences" in html
    assert "Brico Dépôt : offres live" not in html

    health = client.get("/api/health").json()
    assert health["geocoding_provider"] == "fake"
    assert health["bricodepot_live_enabled"] is False
    assert "bricodepot_live" not in health["data_sources"]


def test_geopf_mode_accepts_real_address_ui(database_url):
    app = create_app(
        Settings(
            database_url=database_url,
            seed_on_start=True,
            geocoding_provider="geopf",
            bricodepot_live_enabled=False,
        )
    )
    with TestClient(app) as client:
        html = _home(client)
        assert 'data-geocoding-provider="geopf"' in html
        assert "Saisissez l’adresse du chantier." in html
        assert "Géocodage simulé" not in html
        assert 'list="demo-addresses"' not in html
        assert 'id="demo-addresses"' not in html
        assert "Géocodage d’adresse via Géoplateforme" in html

        health = client.get("/api/health").json()
        assert health["geocoding_provider"] == "geopf"
        assert health["bricodepot_live_enabled"] is False


def test_brico_live_notice_wording(database_url):
    app = create_app(
        Settings(
            database_url=database_url,
            seed_on_start=True,
            geocoding_provider="geopf",
            bricodepot_live_enabled=True,
        )
    )
    with TestClient(app) as client:
        html = _home(client)
        notice = html.split('id="comparator-notice"', 1)[1].split("</div>", 1)[0]
        notice_flat = " ".join(notice.split())
        assert 'data-bricodepot-live-enabled="true"' in html
        assert "Brico Dépôt : offres live activées" in notice_flat
        assert "sources de démonstration possibles" in notice_flat
        assert "prix, stocks et trajets simulés" not in notice_flat

        health = client.get("/api/health").json()
        assert health["bricodepot_live_enabled"] is True
        assert "bricodepot_live" in health["data_sources"]
        assert health["version"] == APP_VERSION


def test_app_js_gates_demo_address_block_on_fake_mode():
    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "function isFakeGeocoding()" in app_js
    assert "comparatorRuntimeConfig" in app_js
    assert "isFakeGeocoding()" in app_js
    # Le message demo reste disponible pour le mode fake uniquement.
    assert "géocodeur de démonstration" in app_js
    assert "isFakeGeocoding() &&" in app_js or "isFakeGeocoding()\n" in app_js
