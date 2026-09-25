"""Comparateur : mode ponctuel complet vs mode chantier compact."""

from decimal import Decimal
from pathlib import Path

from app.version import APP_VERSION


def test_normal_mode_keeps_full_comparator_shell(client):
    home = client.get("/").text
    assert "Préparez votre liste" in home
    assert 'id="origin-title"' in home
    assert "Point de départ" in home
    assert 'value="site"' in home
    assert 'value="current_location"' in home
    assert 'value="company"' in home
    assert 'value="other"' in home
    assert "Chantier" in home
    assert "Entreprise" in home
    assert "Autre adresse" in home
    assert "Ma" in home and "position" in home
    assert 'id="cart-title"' in home
    assert "Votre liste de matériaux" in home
    assert "Enregistrer comme chantier" in home
    assert "Comparer mon panier" in home
    assert 'id="origin-summary"' in home
    assert 'id="needs-summary"' in home
    # Résumés présents mais repliés / cachés hors mode chantier (attribut HTML).
    assert 'hidden' in home.split('id="origin-summary"', 1)[1].split(">", 1)[0]
    assert 'hidden' in home.split('id="needs-summary"', 1)[1].split(">", 1)[0]
    assert 'hidden' in home.split('id="chantier-banner"', 1)[1].split(">", 1)[0]
    assert APP_VERSION == "0.4.0"
    assert f"?v={APP_VERSION}" in home or f"v={APP_VERSION}" in home


def test_chantier_mode_markup_and_aria_hooks(client):
    home = client.get("/").text
    assert "Comparaison pour :" in home
    assert 'id="chantier-banner-name"' in home
    assert 'id="chantier-context-adresse"' in home
    assert 'id="chantier-context-count"' in home
    assert 'id="origin-expand"' in home
    assert 'id="origin-collapse"' in home
    assert 'id="needs-expand"' in home
    assert 'id="needs-collapse"' in home
    assert 'aria-expanded="false"' in home.split('id="origin-expand"', 1)[1].split(
        "</button>", 1
    )[0]
    assert 'aria-controls="origin-detail"' in home
    assert 'aria-controls="needs-detail"' in home
    assert "Modifier la liste" in home
    assert "Comparer les prix" in Path("app/static/app.js").read_text(encoding="utf-8")


def test_compact_mode_js_wiring():
    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "enterChantierCompactMode" in app_js
    assert "exitChantierCompactMode" in app_js
    assert "setOriginExpanded" in app_js
    assert "setNeedsExpanded" in app_js
    assert "refreshNeedsSummary" in app_js
    assert "refreshOriginSummary" in app_js
    assert 'classList.add("comparator-chantier-mode")' in app_js
    assert "setOriginExpanded(false)" in app_js
    assert "setNeedsExpanded(false)" in app_js
    assert '$("origin-expand")' in app_js
    assert '$("needs-expand")' in app_js
    assert '$("origin-collapse")' in app_js
    assert '$("needs-collapse")' in app_js
    # Après comparaison réussie : repli des sections en mode chantier.
    compare = app_js.split('$("compare").addEventListener("click"', 1)[1]
    assert "setOriginExpanded(false)" in compare
    assert "setNeedsExpanded(false)" in compare
    assert "scrollIntoView" in compare
    assert "Enregistrer la liste" in Path("app/templates/index.html").read_text(
        encoding="utf-8"
    )


def test_fiche_finitions_secondary_save_and_discreet_clear():
    detail = Path("app/templates/chantier_detail.html").read_text(encoding="utf-8")
    save = detail.split('id="save-button"', 1)[0]
    assert 'class="button secondary"' in save[-120:]
    clear = detail.split('id="appro-clear"', 1)[0]
    assert "appro-clear-link" in clear[-160:]
    assert "button primary" in detail.split('id="appro-recompare"', 1)[0][-120:]
    js = Path("app/static/chantiers.js").read_text(encoding="utf-8")
    assert "appro-strategy-row" in js
    assert "✓ Solution retenue" in js


def test_compact_mode_live_load_and_compare(client):
    create = client.post(
        "/api/chantiers",
        json={
            "nom": "DUPONT Compact",
            "adresse": "10 rue du Chantier, 75004 Paris",
            "materiaux": [{"product_id": 1, "quantite": "1", "ordre": 0}],
        },
    )
    assert create.status_code == 201, create.text
    chantier = create.json()
    page = client.get(f"/?chantier_id={chantier['id']}")
    assert page.status_code == 200
    assert "app.js" in page.text
    fetched = client.get(f"/api/chantiers/{chantier['id']}")
    assert fetched.status_code == 200
    data = fetched.json()
    assert data["nom"] == "DUPONT Compact"
    assert data["adresse"] == "10 rue du Chantier, 75004 Paris"
    assert len(data["materiaux"]) == 1
    compare = client.post(
        "/api/compare",
        json={
            "lines": [{"product_id": 1, "quantity": "1"}],
            "origin": {"type": "site", "address": data["adresse"]},
        },
    )
    assert compare.status_code == 200, compare.text
    strategies = compare.json()["strategies"]
    assert len(strategies) == 3
    assert {s["key"] for s in strategies} == {
        "single_stop",
        "minimum_materials",
        "best_compromise",
    }


def test_update_list_from_comparator_persists(client):
    create = client.post(
        "/api/chantiers",
        json={
            "nom": "Liste locale",
            "adresse": "10 rue du Chantier, 75004 Paris",
            "materiaux": [{"product_id": 1, "quantite": "1", "ordre": 0}],
        },
    )
    chantier = create.json()
    updated = client.put(
        f"/api/chantiers/{chantier['id']}",
        json={
            "nom": chantier["nom"],
            "client": chantier.get("client"),
            "adresse": chantier["adresse"],
            "date_prevue": chantier.get("date_prevue"),
            "notes": chantier.get("notes"),
            "latitude": chantier.get("latitude"),
            "longitude": chantier.get("longitude"),
            "updated_at": chantier["updated_at"],
            "materiaux": [{"product_id": 1, "quantite": "4", "ordre": 0}],
        },
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert Decimal(body["materiaux"][0]["quantite"]) == Decimal("4")
