"""Suppression chantier + approvisionnement retenu (snapshot comparaison)."""

from decimal import Decimal
from pathlib import Path

from app.services.approvisionnement import STALE_COMPARISON_MESSAGE, materials_fingerprint


def _create(http, **changes):
    payload = {
        "nom": "Appro Test",
        "adresse": "10 rue du Chantier, 75004 Paris",
        "latitude": "48.856600",
        "longitude": "2.352200",
        "materiaux": [{"product_id": 1, "quantite": "10", "ordre": 0}],
        **changes,
    }
    response = http.post("/api/chantiers", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _compare(client, lines, origin=None):
    body = {
        "lines": lines,
        "origin": origin
        or {
            "type": "site",
            "latitude": 48.8566,
            "longitude": 2.3522,
        },
    }
    response = client.post("/api/compare", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _fingerprint_from_materiaux(materiaux):
    class Line:
        def __init__(self, product_id, quantite):
            self.product_id = product_id
            self.quantite = quantite

    return materials_fingerprint(
        [Line(m["product_id"], m["quantite"]) for m in materiaux]
    )


def _put_appro(client, chantier, strategy, comparison, fingerprint=None):
    return client.put(
        f"/api/chantiers/{chantier['id']}/approvisionnement",
        json={
            "updated_at": chantier["updated_at"],
            "needs_fingerprint": fingerprint
            or _fingerprint_from_materiaux(chantier["materiaux"]),
            "strategy": strategy,
            "origin": comparison.get("origin"),
            "cost_parameters": comparison.get("cost_parameters"),
            "currency": comparison.get("currency", "EUR"),
            "tax_basis": comparison.get("tax_basis", "HT"),
        },
    )


def test_delete_ui_hooks_and_messages():
    detail = Path("app/templates/chantier_detail.html").read_text(encoding="utf-8")
    assert 'id="delete-chantier"' in detail
    assert "Supprimer le chantier" in detail
    assert 'id="delete-chantier-panel"' in detail
    assert 'id="delete-chantier-confirm"' in detail
    assert 'id="delete-chantier-cancel"' in detail
    js = Path("app/static/chantiers.js").read_text(encoding="utf-8")
    assert "DELETE" in js
    assert "supprime=1" in js
    assert "Voulez-vous vraiment supprimer" in js


def test_delete_chantier_api_cascade_with_approvisionnement(client):
    chantier = _create(client)
    comparison = _compare(
        client, [{"product_id": 1, "quantity": "10"}]
    )
    strategy = next(s for s in comparison["strategies"] if s["valid"])
    put = _put_appro(client, chantier, strategy, comparison)
    assert put.status_code == 200, put.text
    chantier = client.get(f"/api/chantiers/{chantier['id']}").json()
    deleted = client.delete(
        f"/api/chantiers/{chantier['id']}",
        params={"updated_at": chantier["updated_at"]},
    )
    assert deleted.status_code == 204
    assert client.get(f"/api/chantiers/{chantier['id']}").status_code == 404
    assert (
        client.get(f"/api/chantiers/{chantier['id']}/approvisionnement").status_code
        == 404
    )


def test_delete_conflict_409(client):
    chantier = _create(client)
    stale = chantier["updated_at"]
    updated = client.put(
        f"/api/chantiers/{chantier['id']}",
        json={
            "nom": chantier["nom"],
            "client": None,
            "adresse": chantier["adresse"],
            "date_prevue": None,
            "notes": "touch",
            "latitude": chantier["latitude"],
            "longitude": chantier["longitude"],
            "updated_at": chantier["updated_at"],
            "materiaux": [{"product_id": 1, "quantite": "10", "ordre": 0}],
        },
    ).json()
    conflict = client.delete(
        f"/api/chantiers/{updated['id']}", params={"updated_at": stale}
    )
    assert conflict.status_code == 409
    assert client.get(f"/api/chantiers/{updated['id']}").status_code == 200


def test_delete_404(client):
    response = client.delete(
        "/api/chantiers/999999",
        params={"updated_at": "2020-01-01T00:00:00+00:00"},
    )
    assert response.status_code == 404


def test_approvisionnement_absent(client):
    chantier = _create(client)
    assert (
        client.get(f"/api/chantiers/{chantier['id']}/approvisionnement").status_code
        == 404
    )


def test_choose_each_strategy_and_snapshot(client):
    chantier = _create(
        client,
        materiaux=[
            {"product_id": 1, "quantite": "30", "ordre": 0},
            {"product_id": 2, "quantite": "10", "ordre": 1},
            {"product_id": 3, "quantite": "20", "ordre": 2},
        ],
    )
    comparison = _compare(
        client,
        [
            {"product_id": 1, "quantity": "30"},
            {"product_id": 2, "quantity": "10"},
            {"product_id": 3, "quantity": "20"},
        ],
    )
    valid = [s for s in comparison["strategies"] if s["valid"]]
    assert {s["key"] for s in valid} >= {
        "single_stop",
        "minimum_materials",
        "best_compromise",
    }
    for strategy in valid:
        chantier = client.get(f"/api/chantiers/{chantier['id']}").json()
        response = _put_appro(client, chantier, strategy, comparison)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["strategy_key"] == strategy["key"]
        assert data["obsolete"] is False
        assert data["snapshot"]["strategy"]["key"] == strategy["key"]
        assert data["snapshot"]["strategy"]["lines"]
        assert data["snapshot"]["strategy"]["stops"]
        assert Decimal(str(data["material_total"])) == Decimal(
            str(strategy["material_total"])
        )
        line = data["snapshot"]["strategy"]["lines"][0]
        assert "pack_price" in line and "line_total" in line
        assert "supplier" in line and "supplier_reference" in line


def test_replace_approvisionnement(client):
    chantier = _create(client)
    comparison = _compare(client, [{"product_id": 1, "quantity": "10"}])
    first = next(s for s in comparison["strategies"] if s["key"] == "single_stop" and s["valid"])
    second = next(
        s for s in comparison["strategies"] if s["key"] == "best_compromise" and s["valid"]
    )
    assert _put_appro(client, chantier, first, comparison).status_code == 200
    chantier = client.get(f"/api/chantiers/{chantier['id']}").json()
    replaced = _put_appro(client, chantier, second, comparison)
    assert replaced.status_code == 200
    assert replaced.json()["strategy_key"] == "best_compromise"


def test_clear_approvisionnement_keeps_materials(client):
    chantier = _create(client)
    comparison = _compare(client, [{"product_id": 1, "quantity": "10"}])
    strategy = next(s for s in comparison["strategies"] if s["valid"])
    assert _put_appro(client, chantier, strategy, comparison).status_code == 200
    chantier = client.get(f"/api/chantiers/{chantier['id']}").json()
    cleared = client.delete(
        f"/api/chantiers/{chantier['id']}/approvisionnement",
        params={"updated_at": chantier["updated_at"]},
    )
    assert cleared.status_code == 204
    assert (
        client.get(f"/api/chantiers/{chantier['id']}/approvisionnement").status_code
        == 404
    )
    kept = client.get(f"/api/chantiers/{chantier['id']}").json()
    assert len(kept["materiaux"]) == 1
    assert Decimal(kept["materiaux"][0]["quantite"]) == Decimal("10")


def test_needs_change_marks_obsolete_without_deleting(client):
    chantier = _create(client)
    comparison = _compare(client, [{"product_id": 1, "quantity": "10"}])
    strategy = next(s for s in comparison["strategies"] if s["valid"])
    assert _put_appro(client, chantier, strategy, comparison).status_code == 200
    chantier = client.get(f"/api/chantiers/{chantier['id']}").json()
    edited = client.put(
        f"/api/chantiers/{chantier['id']}",
        json={
            "nom": chantier["nom"],
            "client": None,
            "adresse": chantier["adresse"],
            "date_prevue": None,
            "notes": None,
            "latitude": chantier["latitude"],
            "longitude": chantier["longitude"],
            "updated_at": chantier["updated_at"],
            "materiaux": [{"product_id": 1, "quantite": "15", "ordre": 0}],
        },
    )
    assert edited.status_code == 200
    appro = client.get(f"/api/chantiers/{chantier['id']}/approvisionnement").json()
    assert appro["obsolete"] is True
    assert appro["needs_fingerprint"] == _fingerprint_from_materiaux(
        [{"product_id": 1, "quantite": "10"}]
    )


def test_refuse_stale_comparison_result(client):
    chantier = _create(client)
    comparison = _compare(client, [{"product_id": 1, "quantity": "10"}])
    strategy = next(s for s in comparison["strategies"] if s["valid"])
    # Besoins changés côté serveur avant le PUT
    edited = client.put(
        f"/api/chantiers/{chantier['id']}",
        json={
            "nom": chantier["nom"],
            "client": None,
            "adresse": chantier["adresse"],
            "date_prevue": None,
            "notes": None,
            "latitude": chantier["latitude"],
            "longitude": chantier["longitude"],
            "updated_at": chantier["updated_at"],
            "materiaux": [{"product_id": 1, "quantite": "15", "ordre": 0}],
        },
    ).json()
    response = _put_appro(
        client,
        edited,
        strategy,
        comparison,
        fingerprint=_fingerprint_from_materiaux([{"product_id": 1, "quantite": "10"}]),
    )
    assert response.status_code == 409
    assert STALE_COMPARISON_MESSAGE in response.json()["detail"]
    assert (
        client.get(f"/api/chantiers/{edited['id']}/approvisionnement").status_code
        == 404
    )


def test_approvisionnement_concurrency_conflict(client):
    chantier = _create(client)
    comparison = _compare(client, [{"product_id": 1, "quantity": "10"}])
    strategy = next(s for s in comparison["strategies"] if s["valid"])
    stale = dict(chantier)
    touched = client.put(
        f"/api/chantiers/{chantier['id']}",
        json={
            "nom": chantier["nom"],
            "client": None,
            "adresse": chantier["adresse"],
            "date_prevue": None,
            "notes": "concurrent",
            "latitude": chantier["latitude"],
            "longitude": chantier["longitude"],
            "updated_at": chantier["updated_at"],
            "materiaux": [{"product_id": 1, "quantite": "10", "ordre": 0}],
        },
    ).json()
    response = _put_appro(client, stale, strategy, comparison)
    assert response.status_code == 409
    assert (
        client.get(f"/api/chantiers/{touched['id']}/approvisionnement").status_code
        == 404
    )


def test_comparator_choose_button_only_with_chantier():
    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "Choisir cette solution" in app_js
    assert "loadedChantier" in app_js
    assert "openChoiceConfirm" in app_js
    assert "comparisonFingerprint" in app_js
    assert STALE_COMPARISON_MESSAGE in app_js
    home = Path("app/templates/index.html").read_text(encoding="utf-8")
    assert 'id="choice-confirm-panel"' in home
    # Le bouton est conditionné à loadedChantier dans renderStrategy.
    block = app_js.split("card.append(productsBlock);", 1)[1].split("return card;", 1)[0]
    assert "if (loadedChantier)" in block


def test_detail_approvisionnement_section_hooks():
    detail = Path("app/templates/chantier_detail.html").read_text(encoding="utf-8")
    assert "Approvisionnement retenu" in detail
    assert 'id="appro-empty"' in detail
    assert 'id="appro-recompare"' in detail
    assert 'id="appro-clear"' in detail
    js = Path("app/static/chantiers.js").read_text(encoding="utf-8")
    assert "loadApprovisionnement" in js
    assert "obsolete" in js


def test_openapi_has_approvisionnement_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/chantiers/{chantier_id}/approvisionnement" in paths
    assert {"get", "put", "delete"} <= paths[
        "/api/chantiers/{chantier_id}/approvisionnement"
    ].keys()
