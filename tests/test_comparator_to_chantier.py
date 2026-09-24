"""Comparateur → chantier : création et mise à jour depuis le panier."""

from decimal import Decimal
from pathlib import Path


def _create(http, **changes):
    payload = {
        "nom": "Source Comparateur",
        "adresse": "10 rue du Chantier, 75004 Paris",
        "materiaux": [{"product_id": 1, "quantite": "10", "ordre": 0}],
        **changes,
    }
    response = http.post("/api/chantiers", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_comparator_page_has_save_and_update_hooks(client):
    home = client.get("/").text
    assert 'id="save-as-chantier"' in home
    assert "Enregistrer comme chantier" in home
    assert 'id="update-chantier"' in home
    assert "Mettre à jour le chantier" in home
    assert 'id="save-as-chantier-panel"' in home
    assert 'id="save-as-nom"' in home
    assert 'id="save-as-adresse"' in home
    assert 'id="comparator-meta"' in home
    assert "data-company-address=" in home
    # L'action secondaire n'est pas le CTA principal.
    compare_idx = home.index('id="compare"')
    save_idx = home.index('id="save-as-chantier"')
    assert save_idx < compare_idx


def test_app_js_comparator_to_chantier_wiring():
    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "loadedChantier" in app_js
    assert "syncChantierActions" in app_js
    assert "resolveSaveAsDefaults" in app_js
    assert "openSaveAsPanel" in app_js
    assert "cartMateriauxPayload" in app_js
    assert 'method: "POST"' in app_js
    assert 'method: "PUT"' in app_js
    assert "UPDATE_CONFLICT_MESSAGE" in app_js
    assert (
        "Ce chantier a été modifié depuis son chargement. Rechargez-le avant de poursuivre."
        in app_js
    )
    assert "EMPTY_UPDATE_CONFIRM" in app_js
    assert "Le chantier ne contiendra plus aucun matériau. Continuer ?" in app_js
    assert "current_location" in app_js
    assert "__pmcForceLoadedUpdatedAt" in app_js
    # Pas de sauvegarde auto sur onChange.
    on_change = app_js.split("onChange: () =>", 1)[1].split("},", 1)[0]
    assert "invalidate()" in on_change
    assert "syncChantierActions()" in on_change
    assert "fetch(" not in on_change
    assert "PUT" not in on_change


def test_save_as_defaults_never_invent_address_from_position():
    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    block = app_js.split("function resolveSaveAsDefaults()", 1)[1].split(
        "function hideSaveAsPanel", 1
    )[0]
    assert 'type === "current_location"' in block
    assert 'adresse: ""' in block
    assert "latitude: null" in block
    assert "longitude: null" in block
    assert 'type === "company"' in block
    assert "siteCoordinates" in block
    assert "companyMeta()" in block


def test_create_from_cart_materials_exact(client):
    response = client.post(
        "/api/chantiers",
        json={
            "nom": "Depuis panier",
            "client": "Client A",
            "adresse": "10 rue du Chantier, 75004 Paris",
            "latitude": "48.856600",
            "longitude": "2.352200",
            "materiaux": [
                {"product_id": 1, "quantite": "12", "ordre": 0},
                {"product_id": 7, "quantite": "3.5", "ordre": 1},
            ],
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["nom"] == "Depuis panier"
    assert [m["product_id"] for m in data["materiaux"]] == [1, 7]
    assert Decimal(data["materiaux"][0]["quantite"]) == Decimal("12")
    assert Decimal(data["materiaux"][1]["quantite"]) == Decimal("3.5")
    fetched = client.get(f"/api/chantiers/{data['id']}").json()
    assert len(fetched["materiaux"]) == 2
    page = client.get(f"/chantiers/{data['id']}")
    assert page.status_code == 200


def test_update_from_comparator_preserves_meta_and_replaces_materials(client):
    chantier = _create(
        client,
        client="Meta Client",
        notes="Ne pas perdre",
        date_prevue="2026-10-01",
        materiaux=[{"product_id": 1, "quantite": "10", "ordre": 0}],
    )
    response = client.put(
        f"/api/chantiers/{chantier['id']}",
        json={
            "nom": chantier["nom"],
            "client": chantier["client"],
            "adresse": chantier["adresse"],
            "date_prevue": chantier["date_prevue"],
            "notes": chantier["notes"],
            "latitude": chantier["latitude"],
            "longitude": chantier["longitude"],
            "updated_at": chantier["updated_at"],
            "materiaux": [
                {"product_id": 1, "quantite": "12", "ordre": 0},
                {"product_id": 2, "quantite": "4", "ordre": 1},
            ],
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["client"] == "Meta Client"
    assert data["notes"] == "Ne pas perdre"
    assert data["date_prevue"] == "2026-10-01"
    assert [m["product_id"] for m in data["materiaux"]] == [1, 2]
    assert Decimal(data["materiaux"][0]["quantite"]) == Decimal("12")


def test_update_empty_cart_allowed_with_confirmation_hook(client):
    chantier = _create(client)
    response = client.put(
        f"/api/chantiers/{chantier['id']}",
        json={
            "nom": chantier["nom"],
            "client": chantier["client"],
            "adresse": chantier["adresse"],
            "date_prevue": chantier["date_prevue"],
            "notes": chantier["notes"],
            "latitude": chantier["latitude"],
            "longitude": chantier["longitude"],
            "updated_at": chantier["updated_at"],
            "materiaux": [],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["materiaux"] == []
    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "window.confirm(EMPTY_UPDATE_CONFIRM)" in app_js


def test_update_conflict_409_does_not_overwrite(client):
    chantier = _create(client)
    first = client.put(
        f"/api/chantiers/{chantier['id']}",
        json={
            "nom": chantier["nom"],
            "client": chantier["client"],
            "adresse": chantier["adresse"],
            "date_prevue": None,
            "notes": "gagnant",
            "latitude": None,
            "longitude": None,
            "updated_at": chantier["updated_at"],
            "materiaux": [{"product_id": 1, "quantite": "99", "ordre": 0}],
        },
    )
    assert first.status_code == 200
    stale = client.put(
        f"/api/chantiers/{chantier['id']}",
        json={
            "nom": chantier["nom"],
            "client": chantier["client"],
            "adresse": chantier["adresse"],
            "date_prevue": None,
            "notes": "perdant",
            "latitude": None,
            "longitude": None,
            "updated_at": chantier["updated_at"],
            "materiaux": [{"product_id": 1, "quantite": "1", "ordre": 0}],
        },
    )
    assert stale.status_code == 409
    current = client.get(f"/api/chantiers/{chantier['id']}").json()
    assert current["notes"] == "gagnant"
    assert Decimal(current["materiaux"][0]["quantite"]) == Decimal("99")


def test_no_autosave_contract_in_comparator_js():
    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    # La mise à jour n'est déclenchée que par le clic explicite.
    update_handler = app_js.split('$("update-chantier").addEventListener("click"', 1)[1]
    assert 'method: "PUT"' in update_handler.split("async function init", 1)[0]
    assert "loadedChantier.updated_at" in update_handler


def test_create_rejects_unknown_product_without_losing_contract(client):
    response = client.post(
        "/api/chantiers",
        json={
            "nom": "Produit fantôme",
            "adresse": "10 rue du Chantier, 75004 Paris",
            "materiaux": [{"product_id": 999999, "quantite": "1", "ordre": 0}],
        },
    )
    assert response.status_code in (400, 404, 422)
    # Le contrat UI conserve le panier côté client (pas de clearCart sur erreur).
    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    create_block = app_js.split('$("save-as-chantier-form").addEventListener("submit"', 1)[
        1
    ].split('$("update-chantier")', 1)[0]
    assert "cart = []" not in create_block
    assert "Votre panier" in app_js or "conservé" in create_block or "status(" in create_block


def test_chantier_to_comparator_still_wired():
    """Non-régression : sens Chantier → Comparateur intact."""
    js = Path("app/static/chantiers.js").read_text(encoding="utf-8")
    assert "/?chantier_id=" in js
    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "loadChantierFromQuery" in app_js
    assert "showChantierBanner" in app_js
