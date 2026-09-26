"""Tests hors-ligne du probe Brico Dépôt — aucune requête Internet."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from tools.bricodepot_probe import (
    ProbeError,
    assert_no_sensitive_cookies,
    build_cookie_header,
    build_context_header,
    build_payload,
    build_request_headers,
    experimental_available_in_store,
    extract_items,
    extract_piece_and_measure_prices,
    find_product_by_sku,
    format_multi_summary,
    format_summary,
    inspect_price_range_raw,
    load_graphql_query,
    normalize_skus,
    parse_multi_response,
    parse_response,
    redact_headers_for_log,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "bricodepot"
SKU = "3334160524579"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_amiens_available_in_store_true():
    summary = parse_response(
        _load("amiens.json"),
        sku=SKU,
        seller_id="10",
        seller_code="2350",
        store_id="1",
    )
    assert summary.stock_status == "IN_STOCK"
    assert summary.stock_quantity == 50
    assert summary.is_salable is True
    assert summary.is_offer_available is True
    assert summary.available_in_store is True
    assert summary.price_ht_piece.value == Decimal("7.75")
    assert summary.price_ttc_piece.value == Decimal("9.3")
    assert summary.price_ht_measure.value == Decimal("2.58")
    assert summary.price_ttc_measure.value == Decimal("3.1")
    text = format_summary(summary)
    assert "available_in_store: True" in text
    assert "stock_status: IN_STOCK" in text
    assert "HT pièce: 7.75 EUR" in text
    assert "TTC pièce: 9.30 EUR" in text
    assert "HT unité de mesure: 2.58 EUR" in text
    assert "TTC unité de mesure: 3.10 EUR" in text


def test_dieppe_available_in_store_false_despite_in_stock_status():
    summary = parse_response(
        _load("dieppe.json"),
        sku=SKU,
        seller_id="133",
        seller_code="2362",
        store_id="1",
    )
    assert summary.stock_status == "IN_STOCK"
    assert summary.stock_quantity == 0
    assert summary.is_salable is False
    assert summary.is_offer_available is False
    assert summary.available_in_store is False


def test_sku_absent():
    payload = _load("amiens.json")
    with pytest.raises(ProbeError, match="absent"):
        parse_response(
            payload,
            sku="DOES-NOT-EXIST",
            seller_id="10",
            seller_code="2350",
            store_id="1",
        )


def test_empty_items():
    payload = {"data": {"products": {"items": []}}}
    with pytest.raises(ProbeError, match="Aucun item"):
        parse_response(
            payload,
            sku=SKU,
            seller_id="10",
            seller_code="2350",
            store_id="1",
        )


def test_invalid_json_path_via_extract():
    with pytest.raises(ProbeError):
        extract_items("not-a-dict")


def test_graphql_errors():
    with pytest.raises(ProbeError, match="GraphQL"):
        extract_items({"errors": [{"message": "boom"}], "data": None})


def test_price_absent_still_parses():
    payload = _load("amiens.json")
    item = payload["data"]["products"]["items"][0]
    del item["price_range"]
    del item["measure_price"]
    summary = parse_response(
        payload,
        sku=SKU,
        seller_id="10",
        seller_code="2350",
        store_id="1",
    )
    assert summary.price_ht_piece.value is None
    assert summary.price_ttc_piece.value is None
    assert summary.available_in_store is True


def test_piece_prices_firefox_paths():
    """Chemins explicites Firefox — pas de calcul depuis m²."""
    item = _load("amiens.json")["data"]["products"]["items"][0]
    ht, ttc, ht_m, ttc_m = extract_piece_and_measure_prices(item)
    assert ht.value == Decimal("7.75")
    assert ttc.value == Decimal("9.3")
    assert ht_m.value == Decimal("2.58")
    assert ttc_m.value == Decimal("3.1")


def test_piece_prices_flat_money_under_tax_branch():
    """Forme plate sous final_price (value/currency directs)."""
    item = {
        "price_range": {
            "minimum_price_excluding_tax": {
                "final_price": {"value": 7.75, "currency": "EUR"}
            },
            "minimum_price_including_tax": {
                "final_price": {"value": 9.3, "currency": "EUR"}
            },
        },
        "measure_price": {
            "measure_price_excluding_tax": {
                "final_price": {"value": 2.58, "currency": "EUR"}
            },
            "measure_price_including_tax": {
                "final_price": {"value": 3.1, "currency": "EUR"}
            },
        },
    }
    ht, ttc, ht_m, ttc_m = extract_piece_and_measure_prices(item)
    assert ht.value == Decimal("7.75")
    assert ttc.value == Decimal("9.3")
    assert ht_m.value == Decimal("2.58")
    assert ttc_m.value == Decimal("3.1")


def test_piece_prices_amount_wrapper():
    item = {
        "price_range": {
            "minimum_price_excluding_tax": {
                "final_price": {"amount": {"value": "7.75", "currency": "EUR"}}
            },
            "minimum_price_including_tax": {
                "final_price": {"amount": {"value": "9.30", "currency": "EUR"}}
            },
        },
        "measure_price": {},
    }
    ht, ttc, ht_m, ttc_m = extract_piece_and_measure_prices(item)
    assert ht.value == Decimal("7.75")
    assert ttc.value == Decimal("9.30")
    assert ht_m.value is None
    assert ttc_m.value is None


def test_never_invent_piece_price_from_measure_times_area():
    """Mesure seule → pièce reste None (2.58 × 3 = 7.74 ≠ 7.75 de toute façon)."""
    item = {
        "content_net_value": "3.0000",
        "content_net_unit_label": "M2",
        "measure_price": {
            "measure_price_excluding_tax": {
                "final_price": {"currency": "EUR", "value": 2.58}
            },
            "measure_price_including_tax": {
                "final_price": {"currency": "EUR", "value": 3.1}
            },
        },
    }
    ht, ttc, ht_m, ttc_m = extract_piece_and_measure_prices(item)
    assert ht_m.value == Decimal("2.58")
    assert ttc_m.value == Decimal("3.1")
    assert ht.value is None
    assert ttc.value is None
    assert ht.value != Decimal("7.74")
    assert ttc.value != Decimal("9.3")


def test_amiens_null_piece_prices_keep_measure():
    """Réponse live-like : price_range.final_price.value = null → pièce —."""
    summary = parse_response(
        _load("amiens_null_piece.json"),
        sku=SKU,
        seller_id="10",
        seller_code="2350",
        store_id="1",
    )
    assert summary.price_ht_measure.value == Decimal("2.58")
    assert summary.price_ttc_measure.value == Decimal("3.1")
    assert summary.price_ht_piece.value is None
    assert summary.price_ttc_piece.value is None
    assert summary.stock_quantity == 50
    text = format_summary(summary)
    assert "HT pièce: —" in text
    assert "TTC pièce: —" in text
    raw = summary.price_range_raw
    assert raw["minimum_price_excluding_tax"]["final_price"]["value"] is None
    assert raw["minimum_price_including_tax"]["final_price"]["value"] is None


def test_inspect_price_range_raw_all_branches():
    item = _load("amiens.json")["data"]["products"]["items"][0]
    raw = inspect_price_range_raw(item)
    assert set(raw.keys()) == {
        "minimum_price",
        "minimum_price_excluding_tax",
        "minimum_price_including_tax",
        "special_price_excluding_tax",
        "special_price_including_tax",
    }
    assert raw["minimum_price_excluding_tax"]["final_price"]["value"] == "7.75"
    assert raw["minimum_price_including_tax"]["final_price"]["value"] == "9.3"
    assert raw["special_price_excluding_tax"] is None


def test_piece_priority_ignores_special_and_minimum_price():
    """HT/TTC pièce = excluding/including tax final_price uniquement."""
    item = {
        "price_range": {
            "minimum_price": {
                "final_price": {"currency": "EUR", "value": 99.0},
            },
            "minimum_price_excluding_tax": {
                "final_price": {"currency": "EUR", "value": 7.75},
            },
            "minimum_price_including_tax": {
                "final_price": {"currency": "EUR", "value": 9.3},
            },
            "special_price_excluding_tax": {
                "final_price": {"currency": "EUR", "value": 1.0},
            },
            "special_price_including_tax": {
                "final_price": {"currency": "EUR", "value": 1.2},
            },
        }
    }
    ht, ttc, _, _ = extract_piece_and_measure_prices(item)
    assert ht.value == Decimal("7.75")
    assert ttc.value == Decimal("9.3")
    raw = inspect_price_range_raw(item)
    assert raw["special_price_excluding_tax"]["final_price"]["value"] == "1.0"
    assert raw["minimum_price"]["final_price"]["value"] == "99.0"


def test_graphql_query_requests_piece_and_measure_price_fields():
    query = load_graphql_query()
    compact = " ".join(query.split())
    assert "fragment ProductPriceFragment" in compact
    assert "fragment PriceRangeFragment" in compact
    assert "fragment ProductCoreFragment" in compact
    assert "fragment ProductMeasurePriceFragment" in compact
    assert "type_of_rounding_value" in compact
    assert "minimum_price {" in compact or "minimum_price{" in compact.replace(" ", "")
    assert "minimum_price_excluding_tax" in compact
    assert "minimum_price_including_tax" in compact
    assert "special_price_excluding_tax" in compact
    assert "special_price_including_tax" in compact
    assert "regular_price" in compact
    assert "discount" in compact
    assert "amount_off" in compact
    assert "measure_price_excluding_tax" in compact
    assert "measure_price_including_tax" in compact
    assert "...ProductPriceFragment" in compact
    payload = build_payload(sku=SKU, query=query)
    assert "ProductPriceFragment" in payload[0]["query"]


def test_stock_quantity_null():
    payload = _load("amiens.json")
    payload["data"]["products"]["items"][0]["stock_quantity"] = None
    summary = parse_response(
        payload,
        sku=SKU,
        seller_id="10",
        seller_code="2350",
        store_id="1",
    )
    assert summary.stock_quantity is None
    assert summary.available_in_store is None


def test_sku_not_first_item():
    payload = {
        "data": {
            "products": {
                "items": [
                    {
                        "sku": "OTHER",
                        "name": "Autre",
                        "stock_status": "IN_STOCK",
                        "stock_quantity": 99,
                        "is_salable": True,
                        "is_offer_available": True,
                    },
                    {
                        "sku": SKU,
                        "name": "Cible",
                        "stock_status": "IN_STOCK",
                        "stock_quantity": 1,
                        "is_salable": True,
                        "is_offer_available": True,
                    },
                ]
            }
        }
    }
    item = find_product_by_sku(extract_items(payload), SKU)
    assert item["name"] == "Cible"
    summary = parse_response(
        payload,
        sku=SKU,
        seller_id="10",
        seller_code="2350",
        store_id="1",
    )
    assert summary.name == "Cible"
    assert summary.stock_quantity == 1


def test_experimental_rule_matrix():
    assert (
        experimental_available_in_store(
            stock_quantity=50, is_salable=True, is_offer_available=True
        )
        is True
    )
    assert (
        experimental_available_in_store(
            stock_quantity=0, is_salable=True, is_offer_available=True
        )
        is False
    )
    assert (
        experimental_available_in_store(
            stock_quantity=5, is_salable=False, is_offer_available=True
        )
        is False
    )
    assert (
        experimental_available_in_store(
            stock_quantity=None, is_salable=True, is_offer_available=True
        )
        is None
    )


def test_header_variants_no_sensitive_cookies():
    h1 = build_request_headers(
        variant="A", seller_id="10", seller_code="2350", store_id="1"
    )
    assert "X-BricoDepot-Context" in h1
    assert "Cookie" not in h1
    assert "sellerId=10" in h1["X-BricoDepot-Context"]

    h2 = build_request_headers(
        variant="B", seller_id="10", seller_code="2350", store_id="1"
    )
    assert "Cookie" in h2
    assert "sellerCode" not in h2["Cookie"]
    assert_no_sensitive_cookies(h2["Cookie"])

    h3 = build_request_headers(
        variant="C",
        seller_id="10",
        seller_code="2350",
        store_id="1",
    )
    assert "sellerCode=2350" in h3["Cookie"]
    assert "X-BricoDepot-Context" in h3

    h4 = build_request_headers(
        variant="D", seller_id="133", seller_code="2362", store_id="1"
    )
    assert "X-BricoDepot-Context" not in h4
    assert "sellerId=133" in h4["Cookie"]
    assert "sellerCode=2362" in h4["Cookie"]

    redacted = redact_headers_for_log(h3)
    assert "2350" not in redacted["Cookie"]
    assert "names=" in redacted["Cookie"]

    # Alias historiques
    assert build_request_headers(
        variant="header", seller_id="10", seller_code="2350", store_id="1"
    )["X-BricoDepot-Context"]


def test_payload_is_array_with_query_variables():
    payload = build_payload(sku=SKU)
    assert isinstance(payload, list)
    assert len(payload) == 1
    op = payload[0]
    assert "query" in op
    assert "queryVariables" in op
    assert "variables" not in op
    qv = op["queryVariables"]
    assert qv["filter"]["sku"]["in"] == [SKU]
    assert qv["currentPage"] == 1
    assert qv["pageSize"] == 10
    assert "postCode" not in qv
    assert "sellerId" not in qv
    assert "is_eligible_for_delivery(postcode:$postCode)" in op["query"].replace(" ", "")


def test_payload_multi_sku_in_filter():
    skus = [SKU, "SKU-FIXTURE-B"]
    payload = build_payload(skus=skus)
    qv = payload[0]["queryVariables"]
    assert qv["filter"]["sku"]["in"] == skus
    assert qv["pageSize"] >= 2


def test_multi_skus_found_in_requested_order_despite_response_order():
    """Réponse : B puis A ; demande : A puis B → found ordonné A, B."""
    result = parse_multi_response(
        _load("multi_skus_reordered.json"),
        skus=[SKU, "SKU-FIXTURE-B"],
        seller_id="10",
        seller_code="2350",
        store_id="1",
    )
    assert result.missing_skus == ()
    assert [s.sku for s in result.found] == [SKU, "SKU-FIXTURE-B"]
    assert result.found[0].price_ht_piece.value == Decimal("7.75")
    assert result.found[1].price_ht_piece.value == Decimal("1.0")
    text = format_multi_summary(result)
    assert "Absents: 0" in text
    assert SKU in text
    assert "SKU-FIXTURE-B" in text


def test_multi_sku_requested_absent_reported():
    result = parse_multi_response(
        _load("multi_skus_reordered.json"),
        skus=[SKU, "DOES-NOT-EXIST"],
        seller_id="10",
        seller_code="2350",
        store_id="1",
    )
    assert result.missing_skus == ("DOES-NOT-EXIST",)
    assert len(result.found) == 1
    assert result.found[0].sku == SKU


def test_multi_empty_items_all_missing():
    result = parse_multi_response(
        {"data": {"products": {"items": []}}},
        skus=[SKU, "SKU-FIXTURE-B"],
        seller_id="10",
        seller_code="2350",
        store_id="1",
    )
    assert result.found == ()
    assert result.missing_skus == (SKU, "SKU-FIXTURE-B")


def test_single_sku_historical_via_parse_response():
    summary = parse_response(
        _load("amiens.json"),
        sku=SKU,
        seller_id="10",
        seller_code="2350",
        store_id="1",
    )
    assert summary.sku == SKU
    assert summary.price_ht_piece.value == Decimal("7.75")


def test_multi_null_price_on_one_does_not_break_other():
    result = parse_multi_response(
        _load("multi_one_null_price.json"),
        skus=[SKU, "SKU-FIXTURE-NULL-PRICE"],
        seller_id="10",
        seller_code="2350",
        store_id="1",
    )
    assert result.missing_skus == ()
    ok = result.by_sku[SKU]
    null_price = result.by_sku["SKU-FIXTURE-NULL-PRICE"]
    assert ok.price_ht_piece.value == Decimal("7.75")
    assert ok.price_ttc_piece.value == Decimal("9.3")
    assert null_price.price_ht_piece.value is None
    assert null_price.price_ttc_piece.value is None
    assert null_price.price_ht_measure.value == Decimal("4.0")
    assert null_price.price_ttc_measure.value == Decimal("5.0")


def test_normalize_skus_dedupes_preserve_order():
    assert normalize_skus(sku_list=[SKU, "B", SKU, "B"]) == [SKU, "B"]


def test_rejects_sensitive_cookie_names():
    with pytest.raises(ProbeError, match="sensible"):
        assert_no_sensitive_cookies("sellerId=10; didomi_token=abc")
    with pytest.raises(ProbeError, match="sensible"):
        assert_no_sensitive_cookies("cartId=xyz; sellerId=10")


def test_context_and_cookie_builders():
    assert build_context_header(seller_id="10", store_id="1").startswith(
        "customerGroupId=0;"
    )
    assert build_cookie_header(seller_id="10", store_id="1") == "sellerId=10; storeId=1"
    assert "sellerCode=2350" in build_cookie_header(
        seller_id="10", store_id="1", seller_code="2350"
    )


def test_response_wrapped_in_array():
    inner = _load("amiens.json")
    summary = parse_response(
        [inner],
        sku=SKU,
        seller_id="10",
        seller_code="2350",
        store_id="1",
    )
    assert summary.stock_quantity == 50
    assert summary.price_ht_piece.value == Decimal("7.75")


def test_http_non_200_message_contract():
    err = ProbeError("HTTP 500: boom")
    assert "HTTP 500" in str(err)

