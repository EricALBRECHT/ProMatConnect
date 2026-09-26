"""Tests hors-ligne du probe dépôts Brico Dépôt — aucune requête Internet."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.bricodepot_stores_probe import (
    StoresProbeError,
    build_payload,
    build_request_headers,
    extract_retailers_block,
    format_stores,
    load_graphql_query,
    main,
    parse_response,
    result_to_jsonable,
    run_stores_probe,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "bricodepot"
INSEE = "34172"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_five_stores_montpellier():
    result = parse_response(_load("stores_montpellier.json"), insee_code=INSEE)
    assert result.total_count == 5
    assert len(result.stores) == 5
    first = result.stores[0]
    assert first.name == "MONTPELLIER"
    assert first.entity_id == "235"
    assert first.hypothesized_seller_id == "235"
    assert first.seller_code == "2399"
    assert first.address == "Chemin du Grand Rondelet"
    assert first.city == "LATTES"
    assert first.postcode == "34970"
    assert first.latitude == pytest.approx(43.579712)
    assert first.longitude == pytest.approx(3.876114)
    assert first.distance_km == pytest.approx(3.8)
    assert first.phone == "04 67 85 67 00"
    text = format_stores(result)
    assert "1. MONTPELLIER" in text
    assert "entity_id: 235" in text
    assert "seller_code: 2399" in text
    assert "distance: 3.80 km" in text


def test_empty_results():
    result = parse_response(_load("stores_empty.json"), insee_code="99999")
    assert result.total_count == 0
    assert result.stores == ()
    assert "(aucun dépôt)" in format_stores(result)


def test_null_coordinates_and_phone():
    result = parse_response(_load("stores_null_fields.json"), insee_code="76217")
    assert len(result.stores) == 1
    store = result.stores[0]
    assert store.entity_id == "133"
    assert store.hypothesized_seller_id == "133"
    assert store.seller_code == "2362"
    assert store.latitude is None
    assert store.longitude is None
    assert store.phone is None
    text = format_stores(result)
    assert "latitude: —" in text
    assert "longitude: —" in text
    assert "téléphone: —" in text


def test_graphql_errors():
    with pytest.raises(StoresProbeError, match="GraphQL"):
        extract_retailers_block({"errors": [{"message": "boom"}], "data": None})


def test_http_non_200_message_contract():
    err = StoresProbeError("HTTP 500: boom")
    assert "HTTP 500" in str(err)


def test_invalid_json_message_contract():
    err = StoresProbeError("JSON invalide: Expecting value")
    assert "JSON invalide" in str(err)


def test_payload_is_array_with_code_variable():
    payload = build_payload(insee_code=INSEE)
    assert isinstance(payload, list)
    assert len(payload) == 1
    op = payload[0]
    assert "query" in op
    assert "queryVariables" in op
    assert op["queryVariables"] == {"code": INSEE}
    assert "variables" not in op
    assert "GetRetailersFromLocation" in op["query"]
    assert "close_to_location" in op["query"]
    assert "entity_id" in op["query"]
    assert "seller_code" in op["query"]


def test_headers_have_no_cookies_or_context():
    headers = build_request_headers()
    assert "Cookie" not in headers
    assert "X-BricoDepot-Context" not in headers
    assert headers["Content-Type"] == "application/json"


def test_graphql_query_requests_retailers():
    query = load_graphql_query()
    compact = "".join(query.split())
    assert "retailers" in compact
    assert "city_insee_code" in compact
    assert "pageSize:5" in compact
    assert "entity_id" in compact
    assert "seller_code" in compact


def test_response_wrapped_in_array():
    inner = _load("stores_montpellier.json")
    result = parse_response([inner], insee_code=INSEE)
    assert len(result.stores) == 5


def test_run_from_fixture_no_network():
    meta, result = run_stores_probe(
        insee_code=INSEE,
        fixture_payload=_load("stores_montpellier.json"),
    )
    assert meta["source"] == "fixture"
    assert result is not None
    assert result.stores[0].name == "MONTPELLIER"


def test_dry_run_no_result():
    meta, result = run_stores_probe(insee_code=INSEE, dry_run=True)
    assert result is None
    assert meta["queryVariables"] == {"code": INSEE}


def test_main_from_fixture_and_json(capsys):
    fixture = FIXTURES / "stores_montpellier.json"
    rc = main(["--insee", INSEE, "--from-fixture", str(fixture), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "MONTPELLIER" in out
    assert '"entity_id": "235"' in out or '"entity_id": "235"' in out.replace(" ", "")


def test_result_jsonable_keeps_identity_notes():
    result = parse_response(_load("stores_montpellier.json"), insee_code=INSEE)
    data = result_to_jsonable(result)
    assert data["identity_notes"]["hypothesized_seller_id"]
    assert data["stores"][0]["entity_id"] == "235"
    assert data["stores"][0]["hypothesized_seller_id"] == "235"
    assert data["stores"][0]["seller_code"] == "2399"


def test_missing_retailers_block():
    with pytest.raises(StoresProbeError, match="retailers"):
        extract_retailers_block({"data": {}})
