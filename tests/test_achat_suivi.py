"""Suivi d'achat persistant (séparé du snapshot de comparaison)."""

from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.models.chantier import AchatSuiviLigne
from app.version import APP_VERSION


def _create(client, **changes):
    payload = {
        "nom": "TEST Suivi Achat v050",
        "adresse": "10 rue du Chantier, 75004 Paris",
        "materiaux": [
            {"product_id": 1, "quantite": "12", "ordre": 0},
            {"product_id": 2, "quantite": "5", "ordre": 1},
        ],
        **changes,
    }
    response = client.post("/api/chantiers", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _compare(client, lines=None):
    body = {
        "lines": lines
        or [
            {"product_id": 1, "quantity": "12"},
            {"product_id": 2, "quantity": "5"},
        ],
        "origin": {"type": "site", "address": "10 rue du Chantier, 75004 Paris"},
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
            "currency": "EUR",
            "tax_basis": "HT",
        },
    )


def _retain(client, strategy_key="single_stop"):
    chantier = _create(client)
    comparison = _compare(client)
    strategy = next(
        s for s in comparison["strategies"] if s["key"] == strategy_key and s["valid"]
    )
    assert _put_appro(client, chantier, strategy, comparison).status_code == 200
    chantier = client.get(f"/api/chantiers/{chantier['id']}").json()
    liste = client.get(f"/api/chantiers/{chantier['id']}/liste-achat").json()
    return chantier, comparison, strategy, liste


def _put_line(client, chantier_id, line_key, body):
    return client.put(
        f"/api/chantiers/{chantier_id}/liste-achat/lignes/{line_key}",
        json=body,
    )


def test_app_version_is_050():
    assert APP_VERSION == "0.9.3"


def test_suivi_initial_not_taken(client):
    _, _, _, liste = _retain(client)
    assert liste["taken_count"] == 0
    assert liste["actual"]["lines_renseignees"] == 0
    line = liste["stores"][0]["lines"][0]
    assert line["line_key"]
    assert line["suivi"]["pris"] is False
    assert line["suivi"]["quantite_reelle"] is None
    assert line["suivi"]["prix_reel"] is None
    assert line["suivi"]["renseigne"] is False


def test_cocher_pris_and_persist(client):
    chantier, _, _, liste = _retain(client)
    line = liste["stores"][0]["lines"][0]
    key = line["line_key"]
    response = _put_line(
        client,
        chantier["id"],
        key,
        {
            "pris": True,
            "quantite_reelle": str(line["packs"]),
            "prix_reel": str(line["pack_price"]),
            "updated_at": None,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["pris"] is True
    assert data["updated_at"]
    assert Decimal(str(data["sous_total_reel"])) == (
        Decimal(str(line["packs"])) * Decimal(str(line["pack_price"]))
    ).quantize(Decimal("0.01"))

    again = client.get(f"/api/chantiers/{chantier['id']}/liste-achat").json()
    assert again["taken_count"] == 1
    tracked = next(
        item
        for store in again["stores"]
        for item in store["lines"]
        if item["line_key"] == key
    )
    assert tracked["suivi"]["pris"] is True
    assert Decimal(str(tracked["suivi"]["quantite_reelle"])) == Decimal(str(line["packs"]))


def test_decocher_pris(client):
    chantier, _, _, liste = _retain(client)
    line = liste["stores"][0]["lines"][0]
    key = line["line_key"]
    first = _put_line(
        client,
        chantier["id"],
        key,
        {"pris": True, "quantite_reelle": "2", "prix_reel": "1.50", "updated_at": None},
    ).json()
    second = _put_line(
        client,
        chantier["id"],
        key,
        {
            "pris": False,
            "quantite_reelle": "2",
            "prix_reel": "1.50",
            "updated_at": first["updated_at"],
        },
    )
    assert second.status_code == 200
    assert second.json()["pris"] is False
    liste = client.get(f"/api/chantiers/{chantier['id']}/liste-achat").json()
    assert liste["taken_count"] == 0


def test_ecart_positif_et_negatif(client):
    chantier, _, _, liste = _retain(client)
    line = liste["stores"][0]["lines"][0]
    key = line["line_key"]
    packs = Decimal(str(line["packs"]))
    pack_price = Decimal(str(line["pack_price"]))
    prevu = Decimal(str(line["line_total"]))

    plus = _put_line(
        client,
        chantier["id"],
        key,
        {
            "pris": True,
            "quantite_reelle": str(packs),
            "prix_reel": str(pack_price + Decimal("1.00")),
            "updated_at": None,
        },
    ).json()
    assert Decimal(str(plus["ecart"])) == (
        packs * (pack_price + Decimal("1.00")) - prevu
    ).quantize(Decimal("0.01"))
    assert Decimal(str(plus["ecart"])) > 0

    minus = _put_line(
        client,
        chantier["id"],
        key,
        {
            "pris": True,
            "quantite_reelle": str(packs),
            "prix_reel": str(max(Decimal("0.01"), pack_price - Decimal("0.50"))),
            "updated_at": plus["updated_at"],
        },
    ).json()
    assert Decimal(str(minus["ecart"])) < 0


def test_ligne_partielle_pas_de_total_complet(client):
    chantier, _, _, liste = _retain(client)
    lines = [item for store in liste["stores"] for item in store["lines"]]
    assert len(lines) >= 2
    a, b = lines[0], lines[1]
    _put_line(
        client,
        chantier["id"],
        a["line_key"],
        {
            "pris": True,
            "quantite_reelle": str(a["packs"]),
            "prix_reel": str(a["pack_price"]),
            "updated_at": None,
        },
    )
    _put_line(
        client,
        chantier["id"],
        b["line_key"],
        {"pris": True, "quantite_reelle": str(b["packs"]), "prix_reel": None, "updated_at": None},
    )
    data = client.get(f"/api/chantiers/{chantier['id']}/liste-achat").json()
    assert data["taken_count"] == 2
    assert data["actual"]["lines_renseignees"] == 1
    assert data["actual"]["material_total_renseigne"] is not None
    assert Decimal(str(data["actual"]["material_total_renseigne"])) == (
        Decimal(str(a["packs"])) * Decimal(str(a["pack_price"]))
    ).quantize(Decimal("0.01"))


def test_validation_prix_et_quantite(client):
    chantier, _, _, liste = _retain(client)
    key = liste["stores"][0]["lines"][0]["line_key"]
    assert (
        _put_line(
            client,
            chantier["id"],
            key,
            {"pris": True, "quantite_reelle": "-1", "prix_reel": "1", "updated_at": None},
        ).status_code
        == 422
    )
    assert (
        _put_line(
            client,
            chantier["id"],
            key,
            {"pris": True, "quantite_reelle": "1", "prix_reel": "-0.01", "updated_at": None},
        ).status_code
        == 422
    )


def test_ligne_inexistante(client):
    chantier, _, _, _ = _retain(client)
    response = _put_line(
        client,
        chantier["id"],
        "999:999",
        {"pris": True, "quantite_reelle": "1", "prix_reel": "1", "updated_at": None},
    )
    assert response.status_code == 404


def test_concurrence_updated_at(client):
    chantier, _, _, liste = _retain(client)
    key = liste["stores"][0]["lines"][0]["line_key"]
    first = _put_line(
        client,
        chantier["id"],
        key,
        {"pris": True, "quantite_reelle": "1", "prix_reel": "1.00", "updated_at": None},
    ).json()
    stale = _put_line(
        client,
        chantier["id"],
        key,
        {
            "pris": False,
            "quantite_reelle": "1",
            "prix_reel": "1.00",
            "updated_at": "2020-01-01T00:00:00+00:00",
        },
    )
    assert stale.status_code == 409
    ok = _put_line(
        client,
        chantier["id"],
        key,
        {
            "pris": False,
            "quantite_reelle": "1",
            "prix_reel": "1.00",
            "updated_at": first["updated_at"],
        },
    )
    assert ok.status_code == 200


def test_obsolete_keeps_suivi(client):
    chantier, _, _, liste = _retain(client)
    line = liste["stores"][0]["lines"][0]
    key = line["line_key"]
    snap_price = Decimal(str(line["pack_price"]))
    _put_line(
        client,
        chantier["id"],
        key,
        {
            "pris": True,
            "quantite_reelle": str(line["packs"]),
            "prix_reel": str(line["pack_price"]),
            "updated_at": None,
        },
    )
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
                {"product_id": 1, "quantite": "13", "ordre": 0},
                {"product_id": 2, "quantite": "5", "ordre": 1},
            ],
        },
    )
    assert updated.status_code == 200
    data = client.get(f"/api/chantiers/{chantier['id']}/liste-achat").json()
    assert data["obsolete"] is True
    tracked = next(
        item
        for store in data["stores"]
        for item in store["lines"]
        if item["line_key"] == key
    )
    assert tracked["suivi"]["pris"] is True
    assert Decimal(str(tracked["pack_price"])) == snap_price
    page = client.get(f"/chantiers/{chantier['id']}/liste-achat")
    assert "ancienne version des besoins" in page.text


def test_remplacement_solution_ne_melange_pas_suivi(client, engine):
    from sqlalchemy.orm import Session

    chantier, comparison, strategy_a, liste = _retain(client, "single_stop")
    line = liste["stores"][0]["lines"][0]
    key_a = line["line_key"]
    token_a = liste["snapshot_token"]
    _put_line(
        client,
        chantier["id"],
        key_a,
        {
            "pris": True,
            "quantite_reelle": "99",
            "prix_reel": "1.11",
            "updated_at": None,
        },
    )

    strategy_b = next(
        s
        for s in comparison["strategies"]
        if s["valid"] and s["key"] == "minimum_materials"
    )
    chantier = client.get(f"/api/chantiers/{chantier['id']}").json()
    assert _put_appro(client, chantier, strategy_b, comparison).status_code == 200

    data = client.get(f"/api/chantiers/{chantier['id']}/liste-achat").json()
    assert data["strategy_key"] == "minimum_materials"
    assert data["snapshot_token"] != token_a
    assert data["taken_count"] == 0
    for store in data["stores"]:
        for item in store["lines"]:
            assert item["suivi"]["pris"] is False
            assert item["suivi"]["quantite_reelle"] is None

    with Session(engine) as session:
        old_rows = list(
            session.scalars(
                select(AchatSuiviLigne).where(
                    AchatSuiviLigne.chantier_id == chantier["id"],
                    AchatSuiviLigne.snapshot_token == token_a,
                )
            )
        )
        assert old_rows
        assert old_rows[0].pris is True
        assert Decimal(str(old_rows[0].quantite_reelle)) == Decimal("99")


def test_snapshot_never_mutated_by_suivi(client):
    chantier, _, strategy, liste = _retain(client)
    line = liste["stores"][0]["lines"][0]
    before = client.get(f"/api/chantiers/{chantier['id']}/approvisionnement").json()
    snap_before = before["snapshot"]["strategy"]["lines"][0]["pack_price"]
    _put_line(
        client,
        chantier["id"],
        line["line_key"],
        {"pris": True, "quantite_reelle": "3", "prix_reel": "99.99", "updated_at": None},
    )
    after = client.get(f"/api/chantiers/{chantier['id']}/approvisionnement").json()
    assert after["snapshot"]["strategy"]["lines"][0]["pack_price"] == snap_before
    assert Decimal(str(after["snapshot"]["strategy"]["lines"][0]["pack_price"])) == Decimal(
        str(strategy["lines"][0]["pack_price"])
    )


def test_delete_chantier_cascades_suivi(client, engine):
    from sqlalchemy.orm import Session

    chantier, _, _, liste = _retain(client)
    line = liste["stores"][0]["lines"][0]
    _put_line(
        client,
        chantier["id"],
        line["line_key"],
        {"pris": True, "quantite_reelle": "1", "prix_reel": "1", "updated_at": None},
    )
    deleted = client.delete(
        f"/api/chantiers/{chantier['id']}",
        params={"updated_at": chantier["updated_at"]},
    )
    assert deleted.status_code == 204
    with Session(engine) as session:
        left = list(
            session.scalars(
                select(AchatSuiviLigne).where(AchatSuiviLigne.chantier_id == chantier["id"])
            )
        )
        assert left == []


def test_ui_hooks_suivi():
    template = Path("app/templates/liste_achat.html").read_text(encoding="utf-8")
    js = Path("app/static/shopping_list.js").read_text(encoding="utf-8")
    assert "Quantité achetée" in template
    assert "Prix payé" in template
    assert "Sous-total réel" in template
    assert "Achats réels" in template
    assert "Matériaux réellement payés" in template
    assert "data-shopping-qty" in template
    assert "inputmode=\"decimal\"" in template
    assert "liste-achat/lignes/" in js
    assert "Enregistré" in js
    assert APP_VERSION == "0.9.3"
