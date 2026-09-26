"""Affichage adresse / enseigne / libellés live — sans changer le moteur."""

from __future__ import annotations

from pathlib import Path

from app.connectors.bricodepot.client import parse_retailers
from app.connectors.bricodepot.connector import SUPPLIER_NAME, SUPPLIER_NAME_ALIASES
from app.services.address_display import (
    format_agency_address_lines,
    format_agency_address_text,
    retailer_address_fields,
    strip_supplier_html,
)
from app.version import APP_VERSION


def test_strip_supplier_html_removes_br_tags():
    raw = "CCial Amiens Sud<br />\n\n\n\nDURY,  80480<br/>\nFrance<br/>\n"
    cleaned = strip_supplier_html(raw)
    assert "<br" not in cleaned.lower()
    assert "CCial Amiens Sud" in cleaned
    assert "France" in cleaned


def test_retailer_prefers_structured_street_over_html_blob():
    address, city, postcode = retailer_address_fields(
        raw_address="CCial Amiens Sud<br />\nDURY,  80480<br/>\nFrance<br/>",
        address_data={
            "street": ["CCial Amiens Sud"],
            "city": "DURY",
            "postcode": "80480",
        },
    )
    assert address == "CCial Amiens Sud"
    assert "<br" not in (address or "").lower()
    assert city == "DURY"
    assert postcode == "80480"


def test_format_agency_address_no_postal_city_duplication():
    # Cas cache ancien : blob HTML encore présent + city/postcode structurés
    lines = format_agency_address_lines(
        address="CCial Amiens Sud<br />\nDURY,  80480<br/>\nFrance<br/>",
        postal_code="80480",
        city="DURY",
        country="France",
    )
    text = "\n".join(lines)
    assert "<br" not in text.lower()
    assert text.count("80480") == 1
    assert sum(1 for line in lines if "dury" in line.casefold()) == 1
    assert sum(1 for line in lines if line.casefold() == "france") == 1


def test_format_clean_street_appends_city_once():
    lines = format_agency_address_lines(
        address="CCial Amiens Sud",
        postal_code="80480",
        city="DURY",
    )
    assert lines[0] == "CCial Amiens Sud"
    assert lines[1] == "80480 Dury"
    assert len(lines) == 2


def test_parse_retailers_uses_clean_address():
    payload = {
        "data": {
            "retailers": {
                "items": [
                    {
                        "entity_id": 10,
                        "seller_code": "2350",
                        "name": "AMIENS",
                        "address": "CCial Amiens Sud<br />\nDURY,  80480<br/>\nFrance<br/>",
                        "distance_from_location": 1.2,
                        "address_data": {
                            "street": ["CCial Amiens Sud"],
                            "city": "DURY",
                            "postcode": "80480",
                            "coordinates": {"latitude": 49.86, "longitude": 2.27},
                        },
                    }
                ],
                "total_count": 1,
            }
        }
    }
    retailers = parse_retailers(payload)
    assert len(retailers) == 1
    assert retailers[0].address == "CCial Amiens Sud"
    assert retailers[0].city == "DURY"
    assert retailers[0].postcode == "80480"
    assert "<br" not in (retailers[0].address or "").lower()


def test_technical_supplier_identities_unchanged():
    assert SUPPLIER_NAME == "BRICO DEPOT"
    assert "BRICO_DEPOT" in SUPPLIER_NAME_ALIASES
    assert "BRICO DEPOT" in SUPPLIER_NAME_ALIASES


def test_app_js_ui_contracts():
    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "formatAgencyAddressLines" in app_js
    assert "formatSupplierDisplayName" in app_js
    assert "filterOptionsForDisplay" in app_js
    assert "Prix simulés" not in app_js
    assert "Données de comparaison · offres live disponibles" in app_js
    assert "Comparaison des offres en cours…" in app_js
    assert "Comparaison des offres simulées" not in app_js
    assert "Prix et stock live" in app_js
    assert "isLiveAgencyKey" in app_js
    assert "innerHTML" not in app_js or app_js.count("innerHTML") == 0
    # Pas d'injection HTML adresse
    assert "agencyAddressParagraph" in app_js
    assert APP_VERSION == "0.9.7"


def test_index_template_default_tax_label():
    html = Path("app/templates/index.html").read_text(encoding="utf-8")
    assert "Prix simulés" not in html
    assert "Données de comparaison" in html


def test_format_agency_address_text_multiline():
    text = format_agency_address_text(
        address="CCial Amiens Sud",
        postal_code="80480",
        city="Dury",
    )
    assert text == "CCial Amiens Sud\n80480 Dury"
