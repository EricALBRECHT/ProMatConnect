"""Régressions UX TTC + conditionnement + catalogue national ≠ arrêt physique."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.connectors.base import AgencyData, ConnectorOffer
from app.schemas.catalog import ProductRead
from app.schemas.comparison import CartLine
from app.services.comparison import ComparisonService
from app.version import APP_VERSION


class _Stub:
    def __init__(self, name, offers):
        self.name, self.offers = name, offers

    @property
    def supplier_name(self):
        return self.name

    def get_offers(self, product_ids):
        return [o for o in self.offers if o.product_id in product_ids]


def _national_rail_offer():
    return ConnectorOffer(
        supplier="LEROY_MERLIN",
        product_id=21,
        price=Decimal("25.90"),
        stock=0,
        reference_quantity=Decimal("10"),
        supplier_reference="85343284",
        supplier_unit="lot",
        reference_unit="pièce",
        packaging_quantity=Decimal("10"),
        preparation_minutes=60,
        updated_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        agency=AgencyData(
            id=10,
            name="LEROY_MERLIN (catalogue national)",
            address="",
            postal_code="",
            city="",
            latitude=None,
            longitude=None,
        ),
        tax_basis="TTC",
    )


def _national_plaque_offer():
    return ConnectorOffer(
        supplier="LEROY_MERLIN",
        product_id=1,
        price=Decimal("8.37"),
        stock=0,
        reference_quantity=Decimal("1"),
        supplier_reference="70505960",
        supplier_unit="plaque",
        reference_unit="pièce",
        packaging_quantity=Decimal("1"),
        preparation_minutes=60,
        updated_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        agency=AgencyData(
            id=11,
            name="LEROY_MERLIN (catalogue national)",
            address="",
            postal_code="",
            city="",
            latitude=None,
            longitude=None,
        ),
        tax_basis="TTC",
    )


def _products():
    return {
        1: ProductRead(
            id=1,
            code="PMC-BA13-STD-2500X1200",
            name="Plaque BA13 standard",
            category="Plaques",
            reference_unit="pièce",
            description=None,
        ),
        21: ProductRead(
            id=21,
            code="PMC-RAIL-R48-3000",
            name="Rail R48 3 m",
            category="Ossature",
            reference_unit="pièce",
            description=None,
        ),
    }


def test_ttc_preserved_on_selected_lines_and_strategy():
    offers = [_national_plaque_offer(), _national_rail_offer()]
    service = ComparisonService(
        [_Stub("LEROY_MERLIN", offers)],
        48.8566,
        2.3522,
        tax_basis="TTC",
    )
    result = service.compare(
        [
            CartLine(product_id=1, quantity=Decimal("1")),
            CartLine(product_id=21, quantity=Decimal("1")),
        ],
        _products(),
    )
    assert result.tax_basis == "TTC"
    minimum = next(s for s in result.strategies if s.key == "minimum_materials")
    assert minimum.valid is True
    assert minimum.material_total == Decimal("34.27")
    assert all(line.tax_basis == "TTC" for line in minimum.lines)
    assert all(stop.is_geolocated is False for stop in minimum.stops)
    assert all(stop.is_national_catalog is True for stop in minimum.stops)
    assert minimum.route is None
    assert minimum.total_distance_km is None


def test_rail_lot10_need_1_and_11_conditioning():
    service = ComparisonService(
        [_Stub("LEROY_MERLIN", [_national_rail_offer()])],
        48.8566,
        2.3522,
        tax_basis="TTC",
    )
    products = _products()
    one = service.compare([CartLine(product_id=21, quantity=Decimal("1"))], products)
    line1 = next(s for s in one.strategies if s.key == "minimum_materials").lines[0]
    assert line1.packs == 1
    assert line1.purchased_quantity == Decimal("10")
    assert line1.packaging_quantity == Decimal("10")
    assert line1.reference_quantity == Decimal("10")
    assert line1.supplier_unit == "lot"
    assert line1.line_total == Decimal("25.90")
    assert line1.tax_basis == "TTC"

    eleven = service.compare([CartLine(product_id=21, quantity=Decimal("11"))], products)
    line11 = next(s for s in eleven.strategies if s.key == "minimum_materials").lines[0]
    assert line11.packs == 2
    assert line11.purchased_quantity == Decimal("20")
    assert line11.line_total == Decimal("51.80")


def test_plaque_individual_conditioning_in_snapshot():
    service = ComparisonService(
        [_Stub("LEROY_MERLIN", [_national_plaque_offer()])],
        48.8566,
        2.3522,
        tax_basis="TTC",
    )
    result = service.compare([CartLine(product_id=1, quantity=Decimal("1"))], _products())
    line = next(s for s in result.strategies if s.key == "minimum_materials").lines[0]
    assert line.packs == 1
    assert line.purchased_quantity == Decimal("1")
    assert line.supplier_unit == "plaque"
    assert line.packaging_quantity == Decimal("1")
    assert line.reference_quantity == Decimal("1")
    assert line.line_total == Decimal("8.37")
    assert line.tax_basis == "TTC"


def test_national_stops_flagged_not_geolocated():
    service = ComparisonService(
        [_Stub("LEROY_MERLIN", [_national_rail_offer()])],
        48.8566,
        2.3522,
        tax_basis="TTC",
    )
    result = service.compare([CartLine(product_id=21, quantity=Decimal("1"))], _products())
    minimum = next(s for s in result.strategies if s.key == "minimum_materials")
    assert len(minimum.stops) == 1
    assert minimum.stops[0].is_geolocated is False
    assert minimum.stops[0].is_national_catalog is True
    assert minimum.stops[0].distance_km is None
    # Les stratégies trajet restent indisponibles (pas d'arrêt physique).
    assert next(s for s in result.strategies if s.key == "single_stop").valid is False


def test_snapshot_conditioning_survives_retain(client, session):
    """Après rétention, le snapshot conserve packs / packaging / tax_basis TTC."""
    from sqlalchemy import select

    from app.models import Product
    from scripts.normalized_catalog import seed_normalized_catalog

    seed_normalized_catalog(session)
    session.commit()
    plaque = session.scalar(select(Product).where(Product.code == "PMC-BA13-STD-2500X1200"))
    rail = session.scalar(select(Product).where(Product.code == "PMC-RAIL-R48-3000"))
    assert plaque and rail

    # Offres seed sont HT géolocalisées — on force une comparaison TTC via API
    # sur le catalogue seed (base HT). Ici on vérifie plutôt le snapshot manuel
    # construit comme le front le ferait après compare national.
    chantier = client.post(
        "/api/chantiers",
        json={
            "nom": "TTC conditioning",
            "adresse": "10 rue du Chantier, 75004 Paris",
            "materiaux": [
                {"product_id": plaque.id, "quantite": "1", "ordre": 0},
                {"product_id": rail.id, "quantite": "1", "ordre": 1},
            ],
        },
    ).json()

    strategy = {
        "key": "minimum_materials",
        "title": "Prix matériaux minimum",
        "valid": True,
        "explanation": "test",
        "material_total": "34.27",
        "estimated_procurement_cost": "34.27",
        "stops": [
            {
                "id": 10,
                "supplier": "LEROY_MERLIN",
                "name": "Catalogue national",
                "address": "",
                "postal_code": "",
                "city": "",
                "distance_km": None,
                "is_geolocated": False,
            }
        ],
        "supplier_count": 1,
        "total_distance_km": None,
        "travel_minutes": None,
        "max_preparation_minutes": 60,
        "total_minutes": None,
        "route": None,
        "lines": [
            {
                "product_id": plaque.id,
                "product_name": plaque.name,
                "reference_unit": "pièce",
                "requested_quantity": "1",
                "purchased_quantity": "1",
                "packs": 1,
                "supplier": "LEROY_MERLIN",
                "supplier_reference": "70505960",
                "supplier_unit": "plaque",
                "agency_id": 10,
                "pack_price": "8.37",
                "line_total": "8.37",
                "available_quantity": "1000000",
                "preparation_minutes": 60,
                "updated_at": "2026-01-15T00:00:00+00:00",
                "tax_basis": "TTC",
                "packaging_quantity": "1",
                "reference_quantity": "1",
            },
            {
                "product_id": rail.id,
                "product_name": rail.name,
                "reference_unit": "pièce",
                "requested_quantity": "1",
                "purchased_quantity": "10",
                "packs": 1,
                "supplier": "LEROY_MERLIN",
                "supplier_reference": "85343284",
                "supplier_unit": "lot",
                "agency_id": 10,
                "pack_price": "25.90",
                "line_total": "25.90",
                "available_quantity": "1000000",
                "preparation_minutes": 60,
                "updated_at": "2026-01-15T00:00:00+00:00",
                "tax_basis": "TTC",
                "packaging_quantity": "10",
                "reference_quantity": "10",
            },
        ],
        "unavailable": [],
        "cost_breakdown": None,
    }
    fingerprint = f"{plaque.id}:1.000|{rail.id}:1.000"
    put = client.put(
        f"/api/chantiers/{chantier['id']}/approvisionnement",
        json={
            "updated_at": chantier["updated_at"],
            "needs_fingerprint": fingerprint,
            "strategy": strategy,
            "origin": {
                "type": "site",
                "label": "Chantier",
                "address": chantier["adresse"],
                "latitude": 48.8566,
                "longitude": 2.3522,
                "source": "test",
            },
            "currency": "EUR",
            "tax_basis": "TTC",
        },
    )
    assert put.status_code == 200, put.text
    appro = client.get(f"/api/chantiers/{chantier['id']}/approvisionnement").json()
    assert appro["tax_basis"] == "TTC"
    assert Decimal(str(appro["material_total"])) == Decimal("34.27")
    snap_lines = appro["snapshot"]["strategy"]["lines"]
    assert snap_lines[0]["tax_basis"] == "TTC"
    assert snap_lines[1]["packs"] == 1
    assert snap_lines[1]["packaging_quantity"] == "10"
    assert snap_lines[1]["purchased_quantity"] == "10"

    liste = client.get(f"/api/chantiers/{chantier['id']}/liste-achat").json()
    assert liste["tax_basis"] == "TTC"
    assert liste["available"] is True
    rail_line = next(ln for st in liste["stores"] for ln in st["lines"] if ln["product_id"] == rail.id)
    assert rail_line["packs"] == 1
    assert Decimal(str(rail_line["purchased_quantity"])) == Decimal("10")
    assert Decimal(str(rail_line["reference_quantity"])) == Decimal("10")
    assert Decimal(str(rail_line["pack_price"])) == Decimal("25.90")

    page = client.get(f"/chantiers/{chantier['id']}/liste-achat")
    assert page.status_code == 200
    assert "TTC" in page.text
    assert "data-tax-basis=\"TTC\"" in page.text
    assert "lot" in page.text.lower()
    assert "Besoin chantier" in page.text
    assert "Quantité achetée" in page.text
    assert "Prix / conditionnement" in page.text
    # Pas de HT en dur sur les montants (hors texte pédagogique éventuel)
    assert "|money }} HT" not in Path("app/templates/liste_achat.html").read_text(encoding="utf-8")


def test_ui_no_hardcoded_ht_in_retained_and_shopping():
    chantiers_js = Path("app/static/chantiers.js").read_text(encoding="utf-8")
    assert 'money(line.line_total)} HT' not in chantiers_js
    assert 'money(chantier.material_total)} HT' not in chantiers_js
    assert "appro.tax_basis" in chantiers_js
    assert "Besoin :" in chantiers_js
    assert "Quantité achetée" in chantiers_js

    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "Aucun magasin associé" in app_js
    assert "Non géolocalisée" in app_js
    assert "physicalStops" in app_js
    assert 'Arrêts", String(option.stops.length)' not in app_js

    shopping_js = Path("app/static/shopping_list.js").read_text(encoding="utf-8")
    assert "taxBasis" in shopping_js
    assert "${money(sous)} HT" not in shopping_js
    assert "${money(material)} HT" not in shopping_js

    liste_html = Path("app/templates/liste_achat.html").read_text(encoding="utf-8")
    assert "liste.tax_basis" in liste_html
    assert "data-tax-basis" in liste_html
    # Plus de HT en dur hors fallback Jinja
    assert "|money }} HT" not in liste_html


def test_admin_catalogs_responsive_css_guards():
    css = Path("app/static/styles.css").read_text(encoding="utf-8")
    assert ".admin-table-wrap.admin-catalogs-table-wrap" in css
    assert "overflow-x: hidden" in css
    assert ".catalog-cards" in css
    assert "@media (max-width: 390px)" in css
    html = Path("app/templates/admin_fournisseurs.html").read_text(encoding="utf-8")
    assert "datetime_short" in html
    assert 'class="button catalog-map"' in html
    assert APP_VERSION == "0.9.3"
