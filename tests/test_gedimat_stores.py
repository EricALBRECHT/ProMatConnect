"""Référentiel magasins Gedimat : identifiants, distance, idempotence."""

from decimal import Decimal

from app.connectors.gedimat.public_client import cache_identity
from app.connectors.gedimat.stores import (
    own_catalog_id,
    parse_algolia_assignment,
    parse_store_listing,
)
from app.services.gedimat_stores import (
    get_nearest_gedimat_stores,
    resolve_connector_store_id,
    sync_gedimat_stores,
)
from app.connectors.gedimat.stores import PublicGedimatStore

_GOUERY = """
mtmp = map.addMarker('48.9561395', '1.414891');
  mtmp.idtf = '2603';
  mtmp.title = 'Gouéry';
  mtmp.textInfo = '%3Cspan%3EGouéry%3C/span%3E%3Cbr%3EOuvert actuellement%3Cbr%3E%3Cbr%3ERoute de Merey%3Cbr%3EBP 3%3Cbr%3E27640&nbsp;Breuilpont%3Cbr%3E';
  mtmp.magcode = 'MAG_ECOMMERCE';
"""

_CAYREYRE = """
mtmp = map.addMarker('44.500000', '4.500000');
  mtmp.idtf = '2460';
  mtmp.title = 'Cayreyre';
  mtmp.textInfo = '%3Cbr%3EOuvert actuellement%3Cbr%3E1060 route de Vogüé%3Cbr%3E07200&nbsp;Saint-Maurice-d\\'Ardèche%3Cbr%3E';
  mtmp.magcode = 'MAG_NONECOMMERCE';
"""


def test_listing_keeps_url_id_apart_from_coordinates():
    stores = {row.gedimat_id: row for row in parse_store_listing(_GOUERY + _CAYREYRE)}
    gouery = stores[2603]
    assert gouery.name == "Gouéry"
    assert gouery.address == "Route de Merey, BP 3"
    assert gouery.postal_code == "27640"
    assert gouery.city == "Breuilpont"
    assert gouery.ecommerce is True
    assert gouery.store_type == "MAG_ECOMMERCE"
    assert gouery.latitude == Decimal("48.956140")
    cayreyre = stores[2460]
    assert cayreyre.ecommerce is False
    assert cayreyre.postal_code == "07200"
    assert "Ardèche" in cayreyre.city


def test_known_algolia_correspondences():
    assert parse_algolia_assignment("const algoliaIdM = 2069;\nconst isEcommerce = true;").algolia_id == 2069
    assert parse_algolia_assignment("const algoliaIdM = 2525;\nconst isEcommerce = true;").algolia_id == 2525
    cases = (
        (True, 2069, True, 2069),
        (True, 2525, True, 2525),
        (True, 2032, True, 2032),
        (False, 1, False, None),
    )
    for ecommerce, algolia_id, page_ecommerce, expected in cases:
        assert own_catalog_id(
            ecommerce=ecommerce, algolia_id=algolia_id, page_ecommerce=page_ecommerce
        ) == expected


def test_sync_is_idempotent_and_nearest_uses_algolia_id(session):
    listing = [
        PublicGedimatStore(2069, "Nesle", "8 rue Georges Rémy", "80190", "Nesle", Decimal("49.760000"), Decimal("2.910000"), True, "MAG_ECOMMERCE"),
        PublicGedimatStore(2603, "Gouéry", "Route de Merey", "27640", "Breuilpont", Decimal("48.956140"), Decimal("1.414891"), True, "MAG_ECOMMERCE"),
        PublicGedimatStore(2032, "Unibois", "Lure", "70200", "Lure", Decimal("47.680000"), Decimal("6.490000"), True, "MAG_ECOMMERCE"),
        PublicGedimatStore(2460, "Cayreyre", "route de Vogüé", "07200", "Saint-Maurice", Decimal("44.500000"), Decimal("4.500000"), False, "MAG_NONECOMMERCE"),
    ]
    assignments = {
        2069: (2069, True),
        2603: (2525, True),
        2032: (2032, True),
        2460: (1, False),
    }
    first = sync_gedimat_stores(session, listing=listing, assignments=assignments)
    assert (first.created, first.updated, first.unchanged) == (4, 0, 0)
    second = sync_gedimat_stores(session, listing=listing, assignments=assignments)
    assert (second.created, second.updated, second.unchanged) == (0, 0, 4)

    nearest = get_nearest_gedimat_stores(session, 48.956140, 1.414891, limit=2)
    assert nearest[0].gedimat_id == 2603
    assert nearest[0].algolia_store_id == 2525
    assert nearest[0].ecommerce is True
    assert resolve_connector_store_id(session, 2603) == 2525
    assert resolve_connector_store_id(session, 2460) is None
    assert resolve_connector_store_id(session, 9999) == 9999
    assert cache_identity("30891312", 2525) == ("GEDIMAT", "30891312", "2525")
