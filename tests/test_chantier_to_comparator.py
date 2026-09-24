from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Product
from app.models.chantier import Chantier, ChantierMaterial


def _create(client, **changes):
    payload = {
        "nom": "Transfert Comparateur",
        "adresse": "10 rue du Chantier, 75004 Paris",
        "materiaux": [],
        **changes,
    }
    response = client.post("/api/chantiers", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_compare_prices_button_enabled_and_wiring(client):
    chantier = _create(client)
    page = client.get(f"/chantiers/{chantier['id']}").text
    assert 'id="compare-prices"' in page
    assert "disabled" not in page.split('id="compare-prices"', 1)[1].split(">", 1)[0]
    assert "Bientôt" not in page
    js = Path("app/static/chantiers.js").read_text(encoding="utf-8")
    assert "compare-prices" in js
    assert "/?chantier_id=" in js
    assert "saveChantier" in js
    assert "dirty" in js


def test_comparator_page_has_chantier_banner_hooks(client):
    home = client.get("/").text
    assert 'id="chantier-banner"' in home
    assert "Panier chargé depuis" in home
    assert 'id="chantier-banner-link"' in home
    assert "Retour au chantier" in home
    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "loadChantierFromQuery" in app_js
    assert "mergeProductsFromChantier" in app_js
    assert "siteCoordinates" in app_js
    assert "siteAddressNeedsConfirmation" in app_js
    assert "Le chantier demandé est introuvable." in app_js


def test_api_material_includes_product_identity_for_comparator(client):
    data = _create(
        client,
        materiaux=[
            {"product_id": 1, "quantite": "12", "ordre": 0},
            {"product_id": 7, "quantite": "3", "ordre": 1},
        ],
    )
    assert [m["product_id"] for m in data["materiaux"]] == [1, 7]
    assert Decimal(data["materiaux"][0]["quantite"]) == Decimal("12")
    assert Decimal(data["materiaux"][1]["quantite"]) == Decimal("3")
    assert data["materiaux"][0]["product_code"] == "PMC0001"
    assert data["materiaux"][1]["product_code"] == "PMC0007"
    assert data["materiaux"][0]["product_name"]
    assert data["materiaux"][0]["product_category"]
    assert data["materiaux"][0]["unite"]


def test_chantier_with_coordinates_and_empty_materials(client):
    data = _create(
        client,
        latitude="48.856600",
        longitude="2.352200",
        materiaux=[],
    )
    assert data["materiaux"] == []
    assert Decimal(data["latitude"]) == Decimal("48.856600")
    assert Decimal(data["longitude"]) == Decimal("2.352200")
    fetched = client.get(f"/api/chantiers/{data['id']}").json()
    assert fetched["adresse"]
    assert fetched["materiaux"] == []


def test_unknown_address_without_coordinates_still_readable(client):
    data = _create(client, adresse="Adresse inconnue de chantier, Paris")
    assert data["latitude"] is None and data["longitude"] is None
    assert data["adresse"] == "Adresse inconnue de chantier, Paris"
    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "géocodeur de démonstration" in app_js


def test_product_outside_initial_catalog_page_is_exposed_by_api(client, session: Session):
    # Produit créé après le seed, donc hors d'un éventuel sous-ensemble initial.
    product = Product(
        code="PMCEXTRA",
        name="Produit hors page initiale",
        category="Test",
        reference_unit="sac",
    )
    session.add(product)
    session.flush()
    chantier = Chantier(
        nom="Hors catalogue",
        adresse="10 rue du Chantier, 75004 Paris",
        materiaux=[
            ChantierMaterial(product_id=product.id, quantite=Decimal("4.5"), ordre=0)
        ],
    )
    session.add(chantier)
    session.commit()

    catalog = {p["id"] for p in client.get("/api/products?limit=5").json()}
    assert product.id not in catalog

    payload = client.get(f"/api/chantiers/{chantier.id}").json()
    line = payload["materiaux"][0]
    assert line["product_id"] == product.id
    assert line["product_code"] == "PMCEXTRA"
    assert line["product_name"] == "Produit hors page initiale"
    assert Decimal(line["quantite"]) == Decimal("4.5")
    assert "mergeProductsFromChantier" in Path("app/static/app.js").read_text(encoding="utf-8")


def test_comparator_without_chantier_id_unchanged(client):
    home = client.get("/")
    assert home.status_code == 200
    assert "Préparez votre liste" in home.text
    assert 'id="compare"' in home.text
    assert 'name="origin-type"' in home.text
    assert client.get("/?chantier_id=").status_code == 200
    assert client.get("/?chantier_id=abc").status_code == 200
    assert client.get("/?chantier_id=999999").status_code == 200
