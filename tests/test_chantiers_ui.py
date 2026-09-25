from pathlib import Path

CONFLICT_MESSAGE = (
    "Ce chantier a été modifié dans un autre onglet. Rechargez la page avant de poursuivre."
)


def test_navigation_on_comparator_and_chantiers(client):
    home = client.get("/")
    assert home.status_code == 200
    assert 'href="/chantiers"' in home.text
    assert "Comparateur" in home.text
    assert "Mes chantiers" in home.text
    assert "Préparez votre liste" in home.text
    assert 'id="compare"' in home.text

    listing = client.get("/chantiers")
    assert listing.status_code == 200
    assert "Mes chantiers" in listing.text
    assert 'href="/chantiers/nouveau"' in listing.text
    assert "Aucun chantier pour le moment" in listing.text
    assert 'id="chantier-search"' in listing.text
    assert 'id="chantier-sort"' in listing.text
    assert 'data-page="chantiers-list"' in listing.text
    assert 'href="/"' in listing.text


def test_new_chantier_page(client):
    page = client.get("/chantiers/nouveau")
    assert page.status_code == 200
    assert "Nouveau chantier" in page.text
    assert 'id="nom"' in page.text
    assert 'id="client"' in page.text
    assert 'id="adresse"' in page.text
    assert 'id="date_prevue"' in page.text
    assert 'id="notes"' in page.text
    assert 'id="latitude"' in page.text
    assert 'id="longitude"' in page.text
    assert "Créer le chantier" in page.text
    assert 'href="/chantiers"' in page.text


def test_detail_page_for_existing_and_missing_chantier(client):
    created = client.post(
        "/api/chantiers",
        json={"nom": "Interface Dupont", "adresse": "10 rue du Chantier, Paris", "materiaux": []},
    )
    assert created.status_code == 201
    chantier = created.json()
    page = client.get(f"/chantiers/{chantier['id']}")
    assert page.status_code == 200
    assert chantier["nom"] in page.text
    assert 'data-page="chantier-detail"' in page.text
    assert f'data-chantier-id="{chantier["id"]}"' in page.text
    assert "Besoins" in page.text
    assert "Approvisionnement" in page.text
    assert "Zone dangereuse" in page.text
    assert "Enregistrer" in page.text
    assert "Comparer les prix" in page.text
    assert 'id="compare-prices"' in page.text
    assert "Bientôt" not in page.text
    assert client.get("/chantiers/99999").status_code == 404


def test_create_update_materials_and_conflict_via_pages_api(client):
    # Création (formulaire → API), puis page détail, matériaux et conflit updated_at.
    created = client.post(
        "/api/chantiers",
        json={
            "nom": "Chantier UI",
            "client": "Client Test",
            "adresse": "10 rue du Chantier, Paris",
            "materiaux": [],
        },
    ).json()
    detail = client.get(f"/chantiers/{created['id']}")
    assert detail.status_code == 200
    assert "Chantier UI" in detail.text

    updated = client.put(
        f"/api/chantiers/{created['id']}",
        json={
            "nom": "Chantier UI",
            "client": "Client Test",
            "adresse": "10 rue du Chantier, Paris",
            "latitude": None,
            "longitude": None,
            "date_prevue": None,
            "notes": "après ajout",
            "updated_at": created["updated_at"],
            "materiaux": [{"product_id": 1, "quantite": "30", "ordre": 0}],
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    assert len(body["materiaux"]) == 1
    assert body["materiaux"][0]["product_id"] == 1

    emptied = client.put(
        f"/api/chantiers/{created['id']}",
        json={
            "nom": "Chantier UI",
            "client": "Client Test",
            "adresse": "10 rue du Chantier, Paris",
            "latitude": None,
            "longitude": None,
            "date_prevue": None,
            "notes": "vidé",
            "updated_at": body["updated_at"],
            "materiaux": [],
        },
    )
    assert emptied.status_code == 200
    assert emptied.json()["materiaux"] == []

    conflict = client.put(
        f"/api/chantiers/{created['id']}",
        json={
            "nom": "Périmé",
            "client": None,
            "adresse": "10 rue du Chantier, Paris",
            "latitude": None,
            "longitude": None,
            "date_prevue": None,
            "notes": None,
            "updated_at": created["updated_at"],
            "materiaux": [],
        },
    )
    assert conflict.status_code == 409

    listing = client.get("/api/chantiers").json()
    assert any(item["id"] == created["id"] for item in listing)
    assert "Aucun chantier pour le moment" in client.get("/chantiers").text


def test_empty_chantier_list_message_and_assets(client):
    assert client.get("/api/chantiers").json() == []
    page = client.get("/chantiers")
    assert "Aucun chantier pour le moment" in page.text
    assert client.get("/static/materials.js").status_code == 200
    assert client.get("/static/chantiers.js").status_code == 200
    assert CONFLICT_MESSAGE in Path("app/static/chantiers.js").read_text(encoding="utf-8")
    assert "bindMaterialLines" in Path("app/static/materials.js").read_text(encoding="utf-8")
    assert "bindMaterialLines" in Path("app/static/app.js").read_text(encoding="utf-8")


def test_comparator_assets_still_load_shared_materials(client):
    from app.version import APP_VERSION

    home = client.get("/").text
    assert f"/static/materials.js?v={APP_VERSION}" in home
    assert f"/static/app.js?v={APP_VERSION}" in home
    assert client.get("/static/app.js").status_code == 200


def test_compare_button_resyncs_after_materials_js_cart_change():
    """Non-régression : ajout/suppression via bindMaterialLines doit piloter #compare."""
    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "function syncCompareButton" in app_js
    assert ' $("compare").disabled = cart.length === 0' in app_js or (
        '$("compare").disabled = cart.length === 0' in app_js
    )
    on_change_block = app_js.split("onChange: () =>", 1)[1].split("},", 1)[0]
    assert "invalidate()" in on_change_block
    assert "syncCompareButton()" in on_change_block
    finally_block = app_js.split("} finally {", 1)[1].split("});", 1)[0]
    assert "syncCompareButton()" in finally_block
    detail = Path("app/templates/chantier_detail.html").read_text(encoding="utf-8")
    assert 'id="compare-prices"' in detail
    assert "Bientôt" not in detail


def test_quantity_inputs_use_step_one_and_keep_decimal_rules():
    materials = Path("app/static/materials.js").read_text(encoding="utf-8")
    assert "function configureQuantityInput" in materials
    assert 'input.step = "1"' in materials
    assert 'step: "0.001"' not in materials
    assert "function parseQuantity" in materials
    assert "function reportQuantityFields" in materials
    assert 'replace(",", ".")' in materials
    # Sans min HTML : avec min=0.001 + step=1, les flèches feraient 2→2.001.
    assert "input.removeAttribute(\"min\")" in materials
    index = Path("app/templates/index.html").read_text(encoding="utf-8")
    detail = Path("app/templates/chantier_detail.html").read_text(encoding="utf-8")
    assert 'id="quantity"' in index and 'step="1"' in index
    assert 'id="quantity"' in detail and 'step="1"' in detail
    assert "reportQuantityFields" in Path("app/static/app.js").read_text(encoding="utf-8")
    assert "reportQuantityFields" in Path("app/static/chantiers.js").read_text(encoding="utf-8")

    # Miroir des règles parseQuantity (backend inchangé : 3 décimales, > 0).
    def parse_quantity(raw: str):
        normalized = raw.strip().replace(",", ".")
        if not normalized:
            return False
        try:
            value = float(normalized)
        except ValueError:
            return False
        if not (0.001 <= value <= 1000000):
            return False
        fraction = normalized.split(".")[1] if "." in normalized else ""
        return len(fraction) <= 3

    for raw, ok in [
        ("2", True),
        ("2.5", True),
        ("2.25", True),
        ("2.002", True),
        ("2,5", True),
        ("0.0001", False),
        ("0", False),
        ("1000001", False),
        ("2.0001", False),
    ]:
        assert parse_quantity(raw) is ok, raw
