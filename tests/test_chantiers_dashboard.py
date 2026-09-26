"""Tableau de bord Mes chantiers : résumé API, badges, recherche/tri, fiche UX."""

from decimal import Decimal
from pathlib import Path

from app.version import APP_VERSION
from tests.test_approvisionnement import _compare, _create, _put_appro


def test_app_version_is_dashboard_release():
    assert APP_VERSION == "0.9.3"


def test_list_summary_statuses_and_material_total(client):
    none_item = _create(
        client,
        nom="Sans choix",
        materiaux=[{"product_id": 1, "quantite": "2", "ordre": 0}],
    )
    retained = _create(
        client,
        nom="Avec choix",
        client="Martin",
        materiaux=[{"product_id": 1, "quantite": "10", "ordre": 0}],
    )
    comparison = _compare(
        client,
        [{"product_id": 1, "quantity": "10"}],
        origin={
            "type": "site",
            "latitude": float(retained["latitude"]),
            "longitude": float(retained["longitude"]),
        },
    )
    strategy = next(s for s in comparison["strategies"] if s["valid"])
    put = _put_appro(client, retained, strategy, comparison)
    assert put.status_code == 200, put.text
    assert put.json()["material_total"] is not None

    obsolete = _create(
        client,
        nom="Obsolète bientôt",
        materiaux=[{"product_id": 1, "quantite": "10", "ordre": 0}],
    )
    obsolete_cmp = _compare(
        client,
        [{"product_id": 1, "quantity": "10"}],
        origin={
            "type": "site",
            "latitude": float(obsolete["latitude"]),
            "longitude": float(obsolete["longitude"]),
        },
    )
    obsolete_strategy = next(s for s in obsolete_cmp["strategies"] if s["valid"])
    chosen = _put_appro(client, obsolete, obsolete_strategy, obsolete_cmp).json()
    changed = client.put(
        f"/api/chantiers/{obsolete['id']}",
        json={
            "nom": obsolete["nom"],
            "client": None,
            "adresse": obsolete["adresse"],
            "latitude": obsolete["latitude"],
            "longitude": obsolete["longitude"],
            "date_prevue": None,
            "notes": None,
            "updated_at": chosen["chantier_updated_at"],
            "materiaux": [{"product_id": 1, "quantite": "15", "ordre": 0}],
        },
    )
    assert changed.status_code == 200, changed.text

    by_id = {item["id"]: item for item in client.get("/api/chantiers").json()}
    assert by_id[none_item["id"]]["approvisionnement_status"] == "none"
    assert by_id[none_item["id"]]["materiaux_count"] == 1
    assert by_id[none_item["id"]]["material_total"] is None

    retained_row = by_id[retained["id"]]
    assert retained_row["approvisionnement_status"] == "retained"
    assert retained_row["client"] == "Martin"
    assert Decimal(retained_row["material_total"]) == Decimal(str(put.json()["material_total"]))

    obsolete_row = by_id[obsolete["id"]]
    assert obsolete_row["approvisionnement_status"] == "obsolete"
    assert obsolete_row["materiaux_count"] == 1
    assert obsolete_row["material_total"] is not None


def test_list_page_and_detail_markup_for_dashboard(client):
    listing = client.get("/chantiers").text
    assert 'id="chantier-search"' in listing
    assert 'id="chantier-sort"' in listing
    assert "Plus récents" in listing
    assert "Date prévue" in listing

    js = Path("app/static/chantiers.js").read_text(encoding="utf-8")
    assert "APPRO_STATUS_LABELS" in js
    assert "À comparer" in js
    assert "Approvisionnement retenu" in js
    assert "Comparaison à refaire" in js
    assert "chantier-search" in js

    created = client.post(
        "/api/chantiers",
        json={
            "nom": "Fiche UX",
            "client": "Dupont",
            "adresse": "10 rue du Chantier, 75004 Paris",
            "materiaux": [{"product_id": 1, "quantite": "1", "ordre": 0}],
        },
    ).json()
    detail = client.get(f"/chantiers/{created['id']}").text
    assert 'id="identity-compact"' in detail
    assert "Modifier les informations" in detail
    assert 'id="identity-edit"' in detail
    assert 'id="identity-cancel"' in detail
    assert 'id="identity-save"' in detail
    assert "Besoins" in detail
    assert "Approvisionnement" in detail
    assert "Zone dangereuse" in detail
    assert 'id="appro-compare-empty"' in detail
    assert 'id="appro-badge"' in detail
    assert f"/static/chantiers.js?v={APP_VERSION}" in detail
    assert f"/static/styles.css?v={APP_VERSION}" in detail


def test_list_and_detail_css_support_responsive_cards():
    css = Path("app/static/styles.css").read_text(encoding="utf-8")
    assert ".chantier-card" in css
    assert ".list-toolbar" in css
    assert ".badge.status-retained" in css
    assert ".badge.status-obsolete" in css
    assert ".materials-stack" in css
    assert ".danger-zone" in css
    assert "data-label" in css


def test_openapi_list_uses_chantier_list_item(client):
    schema = client.get("/openapi.json").json()
    list_schema = schema["paths"]["/api/chantiers"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert list_schema["type"] == "array"
    ref = list_schema["items"].get("$ref", "")
    assert "ChantierListItem" in ref
    assert schema["info"]["version"] == "0.9.3"
