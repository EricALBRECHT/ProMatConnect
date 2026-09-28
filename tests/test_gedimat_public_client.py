"""Extraction hors ligne des paramètres publics Gedimat. Aucun appel réseau."""

import base64

from app.connectors.gedimat.public_client import cache_identity, parse_public_config


def test_parse_public_config_reads_rotated_key_and_valid_until():
    secret = base64.b64encode(b"abcvalidUntil=1790603924").decode()
    html = f"""
        const searchClient = algoliasearch(
            '6CTB9BZ5FZ',
            '{secret}'
        );
        const algoliaCatalogIndex = 'Catalog';
    """
    config = parse_public_config(html)
    assert config.application_id == "6CTB9BZ5FZ"
    assert config.api_key == secret
    assert config.index_name == "Catalog"
    assert config.valid_until == 1790603924


def test_cache_identity_is_supplier_sku_and_store():
    assert cache_identity("821768", 2069) == ("GEDIMAT", "821768", "2069")
