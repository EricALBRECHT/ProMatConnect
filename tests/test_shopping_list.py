"""Liste d'achat dérivée du snapshot d'approvisionnement retenu."""

from decimal import Decimal
from pathlib import Path

from app.version import APP_VERSION


def _create(client, **changes):
    payload = {
        "nom": "Liste achat TEST",
        "client": "Client Demo",
        "adresse": "10 rue du Chantier, 75004 Paris",
        "materiaux": [
            {"product_id": 1, "quantite": "30", "ordre": 0},
            {"product_id": 2, "quantite": "10", "ordre": 1},
            {"product_id": 3, "quantite": "20", "ordre": 2},
        ],
        **changes,
    }
    response = client.post("/api/chantiers", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _compare(client, lines, origin=None):
    body = {
        "lines": lines,
        "origin": origin
        or {"type": "site", "address": "10 rue du Chantier, 75004 Paris"},
    }
    response = client.post("/api/compare", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _fingerprint(materiaux):
    parts = []
    for line in sorted(materiaux, key=lambda item: int(item["product_id"])):
        quantity = Decimal(str(line["quantite"])).quantize(Decimal("0.001"))
        parts.append(f"{line['product_id']}:{quantity}")
    return "|".join(parts)


def _put_appro(client, chantier, strategy, comparison):
    return client.put(
        f"/api/chantiers/{chantier['id']}/approvisionnement",
        json={
            "updated_at": chantier["updated_at"],
            "needs_fingerprint": _fingerprint(chantier["materiaux"]),
            "strategy": strategy,
            "origin": comparison.get("origin"),
            "cost_parameters": comparison.get("cost_parameters"),
            "currency": comparison.get("currency", "EUR"),
            "tax_basis": comparison.get("tax_basis", "HT"),
        },
    )


def _retain(client, strategy_key="single_stop"):
    chantier = _create(client)
    comparison = _compare(
        client,
        [
            {"product_id": 1, "quantity": "30"},
            {"product_id": 2, "quantity": "10"},
            {"product_id": 3, "quantity": "20"},
        ],
    )
    strategy = next(
        s
        for s in comparison["strategies"]
        if s["key"] == strategy_key and s["valid"]
    )
    assert _put_appro(client, chantier, strategy, comparison).status_code == 200
    chantier = client.get(f"/api/chantiers/{chantier['id']}").json()
    return chantier, comparison, strategy


def test_app_version_is_040():
    assert APP_VERSION == "0.9.3"


def test_liste_achat_without_approvisionnement(client):
    chantier = _create(client, materiaux=[{"product_id": 1, "quantite": "1", "ordre": 0}])
    api = client.get(f"/api/chantiers/{chantier['id']}/liste-achat")
    assert api.status_code == 200
    data = api.json()
    assert data["available"] is False
    assert data["stores"] == []
    assert data["chantier"]["nom"] == "Liste achat TEST"
    page = client.get(f"/chantiers/{chantier['id']}/liste-achat")
    assert page.status_code == 200
    assert "Aucune liste d’achat disponible." in page.text
    assert "Comparer les prix" in page.text
    assert "Retour au chantier" in page.text


def test_liste_achat_single_store_and_lines(client):
    chantier, comparison, strategy = _retain(client, "single_stop")
    data = client.get(f"/api/chantiers/{chantier['id']}/liste-achat").json()
    assert data["available"] is True
    assert data["obsolete"] is False
    assert data["strategy_key"] == "single_stop"
    assert data["price_disclaimer"] == "Prix constatés lors de la comparaison"
    assert len(data["stores"]) >= 1
    assert data["line_count"] == len(strategy["lines"])
    store = data["stores"][0]
    assert store["stop_order"] == 1
    assert store["supplier"]
    assert store["name"]
    assert store["lines"]
    line = store["lines"][0]
    assert "product_id" in line
    assert "product_name" in line
    assert "supplier_reference" in line
    assert "requested_quantity" in line
    assert "purchased_quantity" in line
    assert "packs" in line
    assert "pack_price" in line
    assert "line_total" in line
    assert "product_code" not in line  # absent du snapshot SelectedLine
    snap_line = strategy["lines"][0]
    assert Decimal(str(line["pack_price"])) == Decimal(str(snap_line["pack_price"]))
    assert Decimal(str(line["line_total"])) == Decimal(str(snap_line["line_total"]))
    assert Decimal(str(store["subtotal"])) == sum(
        (Decimal(str(item["line_total"])) for item in store["lines"]),
        Decimal("0"),
    )
    if data["totals"]["material_total"] is not None:
        assert Decimal(str(data["totals"]["material_total"])) == Decimal(
            str(strategy["material_total"])
        )


def test_liste_achat_multi_store_order_follows_stops(client):
    chantier, comparison, strategy = _retain(client, "minimum_materials")
    data = client.get(f"/api/chantiers/{chantier['id']}/liste-achat").json()
    assert data["available"] is True
    stop_ids = [int(s["id"]) for s in strategy["stops"]]
    store_ids = [s["agency_id"] for s in data["stores"]]
    # Les magasins affichés suivent l'ordre des stops (sous-ensemble ordonné).
    filtered = [sid for sid in stop_ids if sid in store_ids]
    assert store_ids == filtered
    assert [s["stop_order"] for s in data["stores"]] == list(
        range(1, len(data["stores"]) + 1)
    )


def test_liste_achat_keeps_snapshot_price_after_offer_change(client, engine):
    """Modification ultérieure d'une offre → la liste garde le prix snapshot."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.models import Offer

    chantier, comparison, strategy = _retain(client, "single_stop")
    snap_price = Decimal(str(strategy["lines"][0]["pack_price"]))
    before = client.get(f"/api/chantiers/{chantier['id']}/liste-achat").json()
    assert Decimal(str(before["stores"][0]["lines"][0]["pack_price"])) == snap_price

    with Session(engine) as session:
        offers = list(session.scalars(select(Offer)))
        assert offers
        for offer in offers:
            offer.price = Decimal("999.99")
        session.commit()

    after = client.get(f"/api/chantiers/{chantier['id']}/liste-achat").json()
    assert Decimal(str(after["stores"][0]["lines"][0]["pack_price"])) == snap_price
    assert Decimal(str(after["stores"][0]["lines"][0]["pack_price"])) != Decimal(
        "999.99"
    )


def test_liste_achat_obsolete_warning_keeps_old_list(client):
    chantier, comparison, strategy = _retain(client, "single_stop")
    snap_price = Decimal(str(strategy["lines"][0]["pack_price"]))
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
            "materiaux": [
                {"product_id": 1, "quantite": "31", "ordre": 0},
                {"product_id": 2, "quantite": "10", "ordre": 1},
                {"product_id": 3, "quantite": "20", "ordre": 2},
            ],
        },
    )
    assert updated.status_code == 200, updated.text
    data = client.get(f"/api/chantiers/{chantier['id']}/liste-achat").json()
    assert data["obsolete"] is True
    assert data["available"] is True
    assert Decimal(str(data["stores"][0]["lines"][0]["pack_price"])) == snap_price
    page = client.get(f"/chantiers/{chantier['id']}/liste-achat")
    assert page.status_code == 200
    assert "ancienne version des besoins" in page.text
    assert "Refaire la comparaison" in page.text


def test_liste_achat_page_ui_hooks(client):
    chantier, _, _ = _retain(client, "single_stop")
    page = client.get(f"/chantiers/{chantier['id']}/liste-achat")
    assert page.status_code == 200
    text = page.text
    assert "LISTE D’ACHAT" in text
    assert "Retour au chantier" in text
    assert 'id="shopping-print"' in text
    assert "Imprimer" in text
    assert 'type="checkbox"' in text
    assert "Pris" in text
    assert 'id="shopping-progress"' in text
    assert "articles pris" in text
    assert "Prix constatés lors de la comparaison" in text
    assert f"/static/shopping_list.js?v={APP_VERSION}" in text
    css = Path("app/static/styles.css").read_text(encoding="utf-8")
    assert "@media print" in css
    assert ".no-print" in css
    js = Path("app/static/shopping_list.js").read_text(encoding="utf-8")
    assert "window.print()" in js
    assert "articles pris" in js
    assert "shopping-progress-done" in text
    assert "Liste complète" in text
    detail_js = Path("app/static/chantiers.js").read_text(encoding="utf-8")
    assert "Voir la liste d’achat" in detail_js
    assert "/liste-achat" in detail_js


def test_liste_achat_unknown_chantier(client):
    assert client.get("/api/chantiers/999999/liste-achat").status_code == 404
    assert client.get("/chantiers/999999/liste-achat").status_code == 404
