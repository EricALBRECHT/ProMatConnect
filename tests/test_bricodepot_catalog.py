"""Tests offline catalogue Brico Dépôt — fixtures HTTP, aucune dépendance réseau."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from collections import Counter

import pytest
from sqlalchemy import func, select

from app.connectors.bricodepot.catalog import (
    VALIDATION_IMPORT_MAX,
    BricoDepotCatalogService,
)
from app.connectors.bricodepot.catalog_dto import BricoDepotDiscoveredProduct
from app.connectors.bricodepot.catalog_enrich import (
    BricoDepotCatalogEnricher,
    chunk_skus,
    item_to_catalog_product,
    parse_catalog_items_by_sku,
    pick_category_path,
)
from app.connectors.bricodepot.catalog_sitemap import (
    BricoDepotSitemapDiscoverer,
    extract_locs,
    is_product_sitemap_url,
    parse_product_url,
)
from app.connectors.bricodepot.client import PRODUCT_SKU_PAGE_SIZE, BricoDepotClient
from app.models import Offer, Product, Supplier, SupplierProduct
from app.services.bricodepot_catalog_import import (
    BricoDepotCatalogImportService,
    resolve_brico_supplier,
    upsert_supplier_product,
)


INDEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.bricodepot.fr/sitemaps/sitemap-categories.xml</loc></sitemap>
  <sitemap><loc>https://www.bricodepot.fr/sitemaps/sitemap-produits-1.xml</loc></sitemap>
  <sitemap><loc>https://www.bricodepot.fr/sitemaps/sitemap-produits-2.xml</loc></sitemap>
  <sitemap><loc>https://www.bricodepot.fr/sitemaps/sitemap-marques.xml</loc></sitemap>
</sitemapindex>
"""

PRODUCTS_1_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.bricodepot.fr/p/3334160524579/plaque-ba13-purelight</loc></url>
  <url><loc>https://www.bricodepot.fr/p/3334160144715/plaque-ba13-standard</loc></url>
  <url><loc>https://www.bricodepot.fr/invalid/no-sku</loc></url>
  <url><loc>https://www.bricodepot.fr/p/3334160524579/plaque-ba13-purelight-dup</loc></url>
  <url><loc>https://example.com/p/1111111111111/foreign</loc></url>
</urlset>
"""

PRODUCTS_2_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.bricodepot.fr/p/3596265336819/laine-verre</loc></url>
  <url><loc>https://www.bricodepot.fr/p/3334160144715/again</loc></url>
</urlset>
"""


class FakeHttp:
    def __init__(self, mapping: dict[str, tuple[int, bytes]]):
        self.mapping = mapping
        self.calls: list[str] = []

    def __call__(self, url: str) -> tuple[int, bytes]:
        self.calls.append(url)
        if url not in self.mapping:
            return 404, b"missing"
        return self.mapping[url]


class RecordingTransport:
    def __init__(self, responses: list[Any]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *, url: str, payload: Any, headers: dict[str, str], timeout: float):
        self.calls.append({"url": url, "payload": payload, "headers": headers})
        if not self.responses:
            raise AssertionError("plus de réponses transport")
        return 200, self.responses.pop(0)


def _catalog_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data": {"products": {"items": items}}}


def _catalog_item(
    sku: str,
    *,
    name: str | None = None,
    ean: str | None = None,
    sap: str | None = None,
    cond: str | None = "La pièce",
    net: str | None = "3.0000",
    unit: str | None = "M2",
    image: str | None = None,
    categories: list[dict[str, Any]] | None = None,
    product_id: int | None = 42,
) -> dict[str, Any]:
    item: dict[str, Any] = {"sku": sku}
    if name is not None:
        item["name"] = name
    if ean is not None:
        item["ean_code"] = ean
    if sap is not None:
        item["sap_easier_code"] = sap
    if cond is not None:
        item["conditionnement_label"] = cond
    if net is not None:
        item["content_net_value"] = net
    if unit is not None:
        item["content_net_unit_label"] = unit
    if image is not None:
        item["images"] = [{"role": "image", "url": image}]
    if categories is not None:
        item["categories"] = categories
    if product_id is not None:
        item["id"] = product_id
    return item


# --- Sitemap ---


def test_extract_locs_and_product_sitemap_filter():
    locs = extract_locs(INDEX_XML)
    assert len(locs) == 4
    products = [u for u in locs if is_product_sitemap_url(u)]
    assert len(products) == 2
    assert all("sitemap-produits-" in u for u in products)


def test_parse_product_url_ok_and_invalid():
    ok = parse_product_url(
        "https://www.bricodepot.fr/p/3334160524579/plaque-ba13-purelight"
    )
    assert ok is not None
    assert ok.supplier_reference == "3334160524579"
    assert ok.slug == "plaque-ba13-purelight"
    assert ok.product_url.endswith("/p/3334160524579/plaque-ba13-purelight")

    assert parse_product_url("https://www.bricodepot.fr/produits/foo") is None
    assert parse_product_url("https://evil.example/p/3334160524579/x") is None
    assert parse_product_url("https://www.bricodepot.fr/p/abc/x") is None


def test_discover_dedup_limit_and_skip_invalid():
    http = FakeHttp(
        {
            "https://www.bricodepot.fr/sitemaps/sitemap.xml": (200, INDEX_XML.encode()),
            "https://www.bricodepot.fr/sitemaps/sitemap-produits-1.xml": (
                200,
                PRODUCTS_1_XML.encode(),
            ),
            "https://www.bricodepot.fr/sitemaps/sitemap-produits-2.xml": (
                200,
                PRODUCTS_2_XML.encode(),
            ),
        }
    )
    disc = BricoDepotSitemapDiscoverer(
        index_url="https://www.bricodepot.fr/sitemaps/sitemap.xml", http_get=http
    )
    all_items = disc.discover_products(limit=None)
    refs = [p.supplier_reference for p in all_items]
    assert refs == ["3334160524579", "3334160144715", "3596265336819"]

    limited = disc.discover_products(limit=2)
    assert len(limited) == 2
    assert [p.supplier_reference for p in limited] == [
        "3334160524579",
        "3334160144715",
    ]


def test_discover_limit_capped_sample():
    # Génère >50 URLs uniques dans un faux sitemap
    urls = [
        f"<url><loc>https://www.bricodepot.fr/p/{3000000000000 + i}/slug-{i}</loc></url>"
        for i in range(80)
    ]
    xml = (
        '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(urls)
        + "</urlset>"
    )
    index = """<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://www.bricodepot.fr/sitemaps/sitemap-produits-1.xml</loc></sitemap>
    </sitemapindex>"""
    http = FakeHttp(
        {
            "https://www.bricodepot.fr/sitemaps/sitemap.xml": (200, index.encode()),
            "https://www.bricodepot.fr/sitemaps/sitemap-produits-1.xml": (200, xml.encode()),
        }
    )
    disc = BricoDepotSitemapDiscoverer(
        index_url="https://www.bricodepot.fr/sitemaps/sitemap.xml", http_get=http
    )
    assert len(disc.discover_products(limit=50)) == 50
    assert VALIDATION_IMPORT_MAX == 500


# --- Magento enrich ---


def test_chunk_skus_max_10():
    chunks = chunk_skus([str(i) for i in range(25)], size=99)
    assert all(len(c) <= PRODUCT_SKU_PAGE_SIZE for c in chunks)
    assert len(chunks) == 3


def test_parse_catalog_reordered_and_missing_sku():
    payload = _catalog_payload(
        [
            _catalog_item("BBB", name="Second"),
            _catalog_item("AAA", name="First"),
        ]
    )
    by_sku = parse_catalog_items_by_sku(payload, requested_skus=["AAA", "BBB", "CCC"])
    assert set(by_sku) == {"AAA", "BBB"}
    assert "CCC" not in by_sku


def test_optional_fields_absent_tolerated():
    dto = item_to_catalog_product(
        {"sku": "3334160524579"},
        discovered=BricoDepotDiscoveredProduct(
            supplier_reference="3334160524579",
            product_url="https://www.bricodepot.fr/p/3334160524579",
            slug=None,
        ),
    )
    assert dto.supplier_reference == "3334160524579"
    assert dto.name is None
    assert dto.ean is None
    assert dto.content_net_value is None


def test_pick_category_path_prefers_merchandising():
    path = pick_category_path(
        [
            {"name": "Top", "url_path": "produits/opbdfr-top-produits"},
            {
                "name": "Plaque",
                "url_path": "produits/materiau-et-gros-oeuvre/isolation-et-cloison/plaque-de-platre",
            },
        ]
    )
    assert path and path.endswith("plaque-de-platre")


def test_enricher_batches_and_order_independent():
    transport = RecordingTransport(
        [
            _catalog_payload(
                [
                    _catalog_item(
                        "SKU02",
                        name="Deux",
                        ean="222",
                        image="https://media.example/2.jpg",
                    ),
                    _catalog_item("SKU01", name="Un", ean="111"),
                ]
            ),
            _catalog_payload([_catalog_item("SKU11", name="Onze", ean="1111")]),
        ]
    )
    client = BricoDepotClient(transport=transport)
    # inject catalog query via enricher
    enricher = BricoDepotCatalogEnricher(
        client,
        catalog_query="query { products { items { sku } } }",
        batch_size=10,
        seller_id="10",
    )
    discovered = [
        BricoDepotDiscoveredProduct(f"SKU{i:02d}", f"https://www.bricodepot.fr/p/SKU{i:02d}", None)
        for i in range(1, 12)
    ]
    # normalize_skus will keep SKU01... — our fake skus are fine
    products, missing = enricher.enrich(discovered[:11])
    assert len(transport.calls) == 2  # 10 + 1
    assert len(products) == 11
    by_ref = {p.supplier_reference: p for p in products}
    assert by_ref["SKU01"].name == "Un"
    assert by_ref["SKU02"].name == "Deux"
    assert "SKU03" in missing


# --- Import DB ---


def test_resolve_supplier_historical_brico_depot(session):
    before = session.scalar(select(func.count()).select_from(Supplier)) or 0
    session.add(Supplier(name="BRICO_DEPOT", source_type="file", source_key="file:x"))
    session.commit()
    resolved = resolve_brico_supplier(session)
    assert resolved.name == "BRICO_DEPOT"
    after = session.scalar(select(func.count()).select_from(Supplier)) or 0
    assert after == before + 1
    # Pas de second fournisseur créé au resolve
    resolve_brico_supplier(session)
    assert (session.scalar(select(func.count()).select_from(Supplier)) or 0) == after


def test_resolve_supplier_brico_space_alias(session):
    before = session.scalar(select(func.count()).select_from(Supplier)) or 0
    session.add(Supplier(name="BRICO DEPOT", source_type="api", source_key="api:x"))
    session.commit()
    resolved = resolve_brico_supplier(session)
    assert resolved.name == "BRICO DEPOT"
    after = session.scalar(select(func.count()).select_from(Supplier)) or 0
    assert after == before + 1
    resolve_brico_supplier(session)
    assert (session.scalar(select(func.count()).select_from(Supplier)) or 0) == after


def test_upsert_create_and_idempotent_preserve_mapping_and_manual(session):
    supplier = resolve_brico_supplier(session)
    product = Product(
        code="PMC_TEST_BRICO",
        name="Test",
        category="test",
        reference_unit="m2",
        description=None,
    )
    session.add(product)
    session.flush()

    from app.connectors.bricodepot.catalog_dto import BricoDepotCatalogProduct

    offers_before = session.scalar(select(func.count()).select_from(Offer)) or 0

    dto = BricoDepotCatalogProduct(
        supplier_reference="3334160524579",
        name="Plaque A",
        ean="3334160524579",
        packaging_label="La pièce",
        content_net_value=Decimal("3"),
        content_net_unit="M2",
        image_url="https://media.example/a.jpg",
    )
    action, _ = upsert_supplier_product(
        session, supplier=supplier, product=dto, catalog_id=None
    )
    assert action == "created"
    sp = session.scalar(
        select(SupplierProduct).where(
            SupplierProduct.supplier_reference == "3334160524579"
        )
    )
    assert sp is not None
    assert sp.product_id is None
    assert sp.reference_quantity == Decimal("1")
    assert sp.packaging_quantity == Decimal("1")

    # Mapping manuel
    sp.product_id = product.id
    sp.correction_source = "manual"
    sp.reference_quantity = Decimal("3")
    sp.reference_unit = "m2"
    session.flush()

    dto2 = BricoDepotCatalogProduct(
        supplier_reference="3334160524579",
        name="Plaque A renommée",
        ean="3334160524579",
        packaging_label="Le pack",
        content_net_value=Decimal("9"),
        content_net_unit="M2",
        image_url="https://media.example/b.jpg",
    )
    action2, preserved = upsert_supplier_product(
        session, supplier=supplier, product=dto2, catalog_id=None
    )
    assert action2 == "updated"
    assert preserved is True
    session.refresh(sp)
    assert sp.product_id == product.id
    assert sp.designation.startswith("Plaque A renommée")
    assert sp.reference_quantity == Decimal("3")  # manuel conservé
    assert sp.reference_unit == "m2"
    assert sp.supplier_unit == "La pièce"
    assert sp.image_url == "https://media.example/b.jpg"

    offers_after = session.scalar(select(func.count()).select_from(Offer)) or 0
    assert offers_after == offers_before


def test_import_service_apply_no_offers_and_limit_clamp(session):
    http = FakeHttp(
        {
            "https://www.bricodepot.fr/sitemaps/sitemap.xml": (200, INDEX_XML.encode()),
            "https://www.bricodepot.fr/sitemaps/sitemap-produits-1.xml": (
                200,
                PRODUCTS_1_XML.encode(),
            ),
            "https://www.bricodepot.fr/sitemaps/sitemap-produits-2.xml": (
                200,
                PRODUCTS_2_XML.encode(),
            ),
        }
    )
    transport = RecordingTransport(
        [
            _catalog_payload(
                [
                    _catalog_item(
                        "3334160144715",
                        name="Std",
                        ean="3334160144715",
                        image="https://media.example/1.jpg",
                    ),
                    _catalog_item(
                        "3334160524579",
                        name="Pure",
                        ean="3334160524579",
                        image="https://media.example/2.jpg",
                    ),
                    _catalog_item(
                        "3596265336819",
                        name="Laine",
                        ean="3596265336819",
                        image="https://media.example/3.jpg",
                    ),
                ]
            )
        ]
    )
    client = BricoDepotClient(transport=transport)
    service = BricoDepotCatalogImportService(
        session,
        catalog_service=BricoDepotCatalogService(
            discoverer=BricoDepotSitemapDiscoverer(
                index_url="https://www.bricodepot.fr/sitemaps/sitemap.xml",
                http_get=http,
            ),
            enricher=BricoDepotCatalogEnricher(
                client, catalog_query="query Q { products { items { sku } } }", seller_id="10"
            ),
        ),
    )
    offers_before = session.scalar(select(func.count()).select_from(Offer)) or 0
    preview = service.preview(limit=50)
    assert preview.discovered == 3
    assert preview.enriched == 3

    with pytest.raises(ValueError):
        service.apply(limit=501)

    result = service.apply(
        limit=3, products=preview.products, missing_skus=preview.missing_skus
    )
    session.commit()
    assert result.created == 3
    assert result.offers_created == 0
    offers_after = session.scalar(select(func.count()).select_from(Offer)) or 0
    assert offers_after == offers_before

    # Réimport idempotent
    result2 = service.apply(
        limit=3, products=preview.products, missing_skus=preview.missing_skus
    )
    session.commit()
    assert result2.created == 0
    assert result2.updated == 0
    assert result2.unchanged == 3
    brico = resolve_brico_supplier(session)
    assert (
        session.scalar(
            select(func.count())
            .select_from(SupplierProduct)
            .where(SupplierProduct.supplier_id == brico.id)
        )
        or 0
    ) == 3
    assert (session.scalar(select(func.count()).select_from(Offer)) or 0) == offers_before


def test_preview_distinguishes_unchanged_vs_update(session):
    from app.connectors.bricodepot.catalog_dto import BricoDepotCatalogProduct
    from app.connectors.bricodepot.catalog import BricoDepotCatalogPipelineResult
    from app.connectors.bricodepot.catalog_dto import BricoDepotDiscoveredProduct
    from app.services.bricodepot_catalog_import import catalog_field_diff

    supplier = resolve_brico_supplier(session)
    dto = BricoDepotCatalogProduct(
        supplier_reference="9999999999999",
        name="Produit Test",
        ean="9999999999999",
        packaging_label="La pièce",
        image_url="https://media.example/x.jpg",
    )
    upsert_supplier_product(session, supplier=supplier, product=dto, catalog_id=None)
    session.commit()

    # Même données → unchanged
    same = BricoDepotCatalogProduct(
        supplier_reference="9999999999999",
        name="Produit Test",
        ean="9999999999999",
        packaging_label="La pièce",
        image_url="https://media.example/x.jpg",
    )
    sp = session.scalar(
        select(SupplierProduct).where(SupplierProduct.supplier_reference == "9999999999999")
    )
    assert catalog_field_diff(sp, same) == {}

    pipeline = BricoDepotCatalogPipelineResult(
        discovered=[
            BricoDepotDiscoveredProduct(
                "9999999999999", "https://www.bricodepot.fr/p/9999999999999", None
            )
        ],
        products=[same],
        missing_skus=[],
    )
    preview = BricoDepotCatalogImportService(session).preview_from_pipeline(pipeline)
    assert preview.would_create == 0
    assert preview.would_update == 0
    assert preview.unchanged == 1

    # Changement designation → update
    changed = BricoDepotCatalogProduct(
        supplier_reference="9999999999999",
        name="Produit Test MODIFIÉ",
        ean="9999999999999",
        packaging_label="La pièce",
        image_url="https://media.example/x.jpg",
    )
    pipeline2 = BricoDepotCatalogPipelineResult(
        discovered=pipeline.discovered,
        products=[changed],
        missing_skus=[],
    )
    preview2 = BricoDepotCatalogImportService(session).preview_from_pipeline(pipeline2)
    assert preview2.would_update == 1
    assert preview2.unchanged == 0

    action, _ = upsert_supplier_product(
        session, supplier=supplier, product=same, catalog_id=None
    )
    assert action == "unchanged"
    action2, _ = upsert_supplier_product(
        session, supplier=supplier, product=changed, catalog_id=None
    )
    assert action2 == "updated"


def test_manual_preserves_unit_and_skips_unit_diff(session):
    from app.connectors.bricodepot.catalog_dto import BricoDepotCatalogProduct
    from app.services.bricodepot_catalog_import import catalog_field_diff

    supplier = resolve_brico_supplier(session)
    dto = BricoDepotCatalogProduct(
        supplier_reference="8888888888888",
        name="Manuel",
        ean="8888888888888",
        packaging_label="La pièce",
        image_url="https://media.example/m.jpg",
    )
    upsert_supplier_product(session, supplier=supplier, product=dto, catalog_id=None)
    sp = session.scalar(
        select(SupplierProduct).where(SupplierProduct.supplier_reference == "8888888888888")
    )
    sp.correction_source = "manual"
    sp.supplier_unit = "plaque"
    sp.reference_quantity = Decimal("3")
    session.flush()

    again = BricoDepotCatalogProduct(
        supplier_reference="8888888888888",
        name="Manuel",
        ean="8888888888888",
        packaging_label="Le pack",  # différent mais manuel → ignoré
        image_url="https://media.example/m.jpg",
    )
    assert "supplier_unit" not in catalog_field_diff(sp, again)
    action, preserved = upsert_supplier_product(
        session, supplier=supplier, product=again, catalog_id=None
    )
    assert action == "unchanged"
    session.refresh(sp)
    assert sp.supplier_unit == "plaque"
    assert sp.reference_quantity == Decimal("3")
    assert preserved is False  # product_id still None


def test_cli_requires_limit_and_rejects_over_max(capsys):
    import tools.bricodepot_catalog_import as cli

    assert cli.main([]) == 2
    err = capsys.readouterr().err
    assert "--limit" in err or "--sample-diverse" in err

    assert cli.main(["--limit", "0"]) == 2
    assert cli.main(["--limit", "-1"]) == 2
    assert cli.main(["--limit", "501"]) == 2
    assert cli.main(["--sample-diverse", "501"]) == 2
    assert cli.main(["--sample-diverse", "0"]) == 2
    assert cli.main(["--apply"]) == 2
    assert cli.main(["--limit", "10", "--sample-diverse", "10"]) == 2
    err2 = capsys.readouterr().err
    assert "mutuellement" in err2 or "exclusifs" in err2 or "Refusé" in err2


def test_clamp_limit_service_bounds():
    from app.services.bricodepot_catalog_import import BricoDepotCatalogImportService

    with pytest.raises(ValueError):
        BricoDepotCatalogImportService._clamp_limit(0)
    with pytest.raises(ValueError):
        BricoDepotCatalogImportService._clamp_limit(501)
    assert BricoDepotCatalogImportService._clamp_limit(500) == 500
    assert BricoDepotCatalogImportService._clamp_limit(1) == 1


# --- Échantillon diversifié déterministe ---


def test_round_robin_deterministic_no_dupes_small_family():
    from app.connectors.bricodepot.catalog_sample import (
        concentration_stats,
        round_robin_sample,
    )

    order = ["alpha", "beta", "gamma"]
    pools = {
        "alpha": ["A1", "A2", "A3", "A4"],
        "beta": ["B1"],  # petite famille
        "gamma": ["G1", "G2", "G3"],
    }
    first = round_robin_sample(order, pools, limit=8)
    second = round_robin_sample(order, pools, limit=8)
    assert first == second
    skus = [s for s, _ in first]
    assert len(skus) == len(set(skus)) == 8
    # Round-robin : A1,B1,G1,A2,G2,A3,G3,A4 — beta épuisée après 1
    assert first == [
        ("A1", "alpha"),
        ("B1", "beta"),
        ("G1", "gamma"),
        ("A2", "alpha"),
        ("G2", "gamma"),
        ("A3", "alpha"),
        ("G3", "gamma"),
        ("A4", "alpha"),
    ]
    counts = Counter(f for _, f in first)
    assert counts["beta"] == 1
    assert counts["alpha"] == 4
    assert counts["gamma"] == 3

    stats = concentration_stats(dict(counts))
    assert stats["families_represented"] == 3
    assert stats["top1_category_pct"] == 50.0  # 4/8
    assert stats["top2_category_pct"] == 87.5  # 4+3
    assert stats["diversity_warning"] is True  # > 70


def test_round_robin_respects_limit_and_exhaustion():
    from app.connectors.bricodepot.catalog_sample import round_robin_sample

    order = ["a", "b"]
    pools = {"a": ["1", "2"], "b": ["3"]}
    got = round_robin_sample(order, pools, limit=500)
    assert got == [("1", "a"), ("3", "b"), ("2", "a")]


def test_round_robin_skips_cross_family_duplicates():
    from app.connectors.bricodepot.catalog_sample import round_robin_sample

    order = ["a", "b"]
    pools = {"a": ["X", "A2"], "b": ["X", "B2"]}
    got = round_robin_sample(order, pools, limit=4)
    assert got == [("X", "a"), ("B2", "b"), ("A2", "a")]


def test_concentration_warning_threshold_strict():
    from app.connectors.bricodepot.catalog_sample import concentration_stats

    # top2 exactement 70 → pas de warning (critère > 70)
    exactly = concentration_stats({"a": 40, "b": 30, "c": 30})
    assert exactly["top2_category_pct"] == 70.0
    assert exactly["diversity_warning"] is False

    over = concentration_stats({"a": 40, "b": 31, "c": 29})
    assert over["top2_category_pct"] == 71.0
    assert over["diversity_warning"] is True


def test_family_slug_and_promo_filter():
    from app.connectors.bricodepot.catalog_sample import (
        family_slug_from_url_path,
        is_promo_family_slug,
    )

    assert family_slug_from_url_path("produits/plomberie/robinet") == "plomberie"
    assert family_slug_from_url_path("produits/outillage") == "outillage"
    assert is_promo_family_slug("op-bdfr-categorie-2") is True
    assert is_promo_family_slug("plomberie") is False


def test_diverse_sampler_list_families_and_sample_offline():
    import base64

    from app.connectors.bricodepot.catalog_sample import BricoDepotDiverseSampler
    from app.connectors.bricodepot.client import BricoDepotClient

    def b64(n: int) -> str:
        return base64.b64encode(str(n).encode("ascii")).decode("ascii")

    uid_plom = b64(8664)
    uid_plom_child = b64(9001)
    uid_out = b64(8872)
    uid_cui = b64(12091)

    def transport(*, url, payload, headers, timeout):
        query = payload[0]["query"]
        variables = payload[0]["queryVariables"]
        if "CategoryFamilies" in query or (
            "categoryList" in query and "url_path" in query and "name" in query
        ):
            return 200, {
                "data": {
                    "categoryList": [
                        {
                            "uid": "root",
                            "name": "Produits",
                            "url_path": "produits",
                            "children": [
                                {
                                    "uid": uid_plom,
                                    "name": "Plomberie",
                                    "url_path": "produits/plomberie",
                                    "product_count": 5,
                                },
                                {
                                    "uid": uid_out,
                                    "name": "Outillage",
                                    "url_path": "produits/outillage",
                                    "product_count": 5,
                                },
                                {
                                    "uid": "promo",
                                    "name": "Promo",
                                    "url_path": "produits/nouveautes",
                                    "product_count": 99,
                                },
                            ],
                        }
                    ]
                }
            }
        if "CategoryChildren" in query:
            assert variables["id"] == "8664"
            return 200, {
                "data": {
                    "categoryList": [
                        {
                            "children": [
                                {"uid": uid_plom_child, "product_count": 5},
                            ]
                        }
                    ]
                }
            }
        if "CategoryByUrlPath" in query or "urlPath" in variables:
            return 200, {
                "data": {
                    "categories": {
                        "items": [
                            {
                                "uid": uid_cui,
                                "name": "Cuisine",
                                "url_path": "produits/cuisine",
                                "product_count": 3,
                            }
                        ]
                    }
                }
            }
        uid = variables["filter"]["category_uid"]["eq"]
        page = variables["currentPage"]
        catalog = {
            uid_plom: [],  # parent vide — fallback enfants
            uid_plom_child: [f"P{i}" for i in range(1, 6)],
            uid_out: [f"O{i}" for i in range(1, 6)],
            uid_cui: [f"C{i}" for i in range(1, 4)],
        }
        items = catalog.get(uid, [])
        size = variables["pageSize"]
        start = (page - 1) * size
        chunk = items[start : start + size]
        total_pages = max(1, (len(items) + size - 1) // size) if items else 0
        return 200, {
            "data": {
                "products": {
                    "total_count": len(items),
                    "page_info": {
                        "current_page": page,
                        "page_size": size,
                        "total_pages": total_pages,
                    },
                    "items": [{"sku": s} for s in chunk],
                }
            }
        }

    client = BricoDepotClient(transport=transport)
    sampler = BricoDepotDiverseSampler(client, page_size=2)
    families = sampler.list_families()
    slugs = [f.family_slug for f in families]
    assert slugs == ["cuisine", "outillage", "plomberie"]
    assert "nouveautes" not in slugs

    sample = sampler.sample_round_robin(limit=7)
    assert not sample.errors
    skus = [d.supplier_reference for d in sample.selected]
    assert len(skus) == 7
    assert len(set(skus)) == 7
    again = sampler.sample_round_robin(limit=7)
    assert [d.supplier_reference for d in again.selected] == skus
    assert sample.family_by_sku[skus[0]] == "cuisine"
    assert sample.family_by_sku[skus[1]] == "outillage"
    assert sample.family_by_sku[skus[2]] == "plomberie"
    assert any(s.startswith("P") for s in skus)
    assert sample.selection_counts.get("plomberie", 0) >= 1


def test_magento_category_id_from_uid():
    from app.connectors.bricodepot.catalog_sample import magento_category_id_from_uid

    assert magento_category_id_from_uid("ODY2NA==") == "8664"


def test_preview_diverse_no_db_write(session):
    """Preview sample_diverse : aucune Offer / Product auto ; compteurs OK."""
    from app.connectors.bricodepot.catalog_sample import (
        BricoDepotDiverseSampler,
        DiverseSampleResult,
        BricoDepotFamily,
    )

    class FakeSampler(BricoDepotDiverseSampler):
        def __init__(self):
            pass

        def sample_round_robin(self, *, limit: int) -> DiverseSampleResult:
            fams = [
                BricoDepotFamily("1", "A", "produits/alpha", "alpha", 10),
                BricoDepotFamily("2", "B", "produits/beta", "beta", 10),
            ]
            selected = []
            family_by_sku = {}
            for i in range(limit):
                fam = fams[i % 2]
                sku = f"SKU{i:04d}"
                selected.append(
                    BricoDepotDiscoveredProduct(
                        sku, f"https://www.bricodepot.fr/p/{sku}", None
                    )
                )
                family_by_sku[sku] = fam.family_slug
            counts = Counter(family_by_sku.values())
            return DiverseSampleResult(
                families=fams,
                selected=selected,
                family_by_sku=family_by_sku,
                family_inventory=[
                    {"family": f.family_slug, "available": f.product_count}
                    for f in fams
                ],
                selection_counts=dict(counts),
                graphql_calls=3,
            )

    class FakeEnricher:
        def enrich(self, discovered):
            products = []
            for d in discovered:
                fam = "alpha" if int(d.supplier_reference[-4:]) % 2 == 0 else "beta"
                products.append(
                    item_to_catalog_product(
                        {
                            "sku": d.supplier_reference,
                            "name": f"Name {d.supplier_reference}",
                            "ean_code": d.supplier_reference,
                            "sap_easier_code": f"SAP{d.supplier_reference[-4:]}",
                            "images": [{"url": "https://cdn.example/x.jpg"}],
                            "conditionnement_label": "La pièce",
                            "content_net_value": "0",
                            "categories": [{"url_path": f"produits/{fam}"}],
                        },
                        discovered=d,
                    )
                )
            return products, []

    supplier = resolve_brico_supplier(session)
    for sku in ("SKU0000", "SKU0001"):
        dto = item_to_catalog_product(
            {
                "sku": sku,
                "name": f"Name {sku}",
                "ean_code": sku,
                "sap_easier_code": f"SAP{sku[-4:]}",
                "images": [{"url": "https://cdn.example/x.jpg"}],
                "conditionnement_label": "La pièce",
                "content_net_value": "0",
                "categories": [
                    {
                        "url_path": (
                            "produits/alpha"
                            if sku.endswith("0")
                            else "produits/beta"
                        )
                    }
                ],
            },
            discovered=BricoDepotDiscoveredProduct(
                sku, f"https://www.bricodepot.fr/p/{sku}", None
            ),
        )
        upsert_supplier_product(session, supplier=supplier, product=dto, catalog_id=None)
    session.flush()

    offers_before = session.scalar(select(func.count()).select_from(Offer)) or 0
    products_before = session.scalar(select(func.count()).select_from(Product)) or 0
    sp_before = session.scalar(select(func.count()).select_from(SupplierProduct)) or 0

    service = BricoDepotCatalogImportService(
        session,
        catalog_service=BricoDepotCatalogService(
            sampler=FakeSampler(),
            enricher=FakeEnricher(),  # type: ignore[arg-type]
        ),
    )
    preview = service.preview(limit=6, sample_diverse=True)
    assert preview.discovered == 6
    assert preview.enriched == 6
    assert preview.sample_mode == "diverse_round_robin"
    assert preview.would_create == 4
    assert preview.unchanged == 2
    assert preview.would_update == 0
    assert preview.graphql_calls_sample == 3

    assert (session.scalar(select(func.count()).select_from(Offer)) or 0) == offers_before
    assert (session.scalar(select(func.count()).select_from(Product)) or 0) == products_before
    assert (
        session.scalar(select(func.count()).select_from(SupplierProduct)) or 0
    ) == sp_before


def test_sample_diverse_ceiling_500():
    from app.connectors.bricodepot.catalog_sample import round_robin_sample

    order = [f"f{i}" for i in range(10)]
    pools = {f: [f"{f}-{j}" for j in range(100)] for f in order}
    got = round_robin_sample(order, pools, limit=500)
    assert len(got) == 500
    assert len({s for s, _ in got}) == 500
    # répartition quasi égale 50/famille
    counts = Counter(f for _, f in got)
    assert set(counts.values()) == {50}
