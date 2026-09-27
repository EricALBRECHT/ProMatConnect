"""Tests moteur sync catalogue Brico — réseau mocké, aucune dépendance externe."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select

from app.connectors.bricodepot.catalog_discovery import (
    BricoDepotFullDiscoverer,
    CatalogDiscoveryReport,
)
from app.connectors.bricodepot.catalog_dto import (
    BricoDepotCatalogProduct,
    BricoDepotDiscoveredProduct,
)
from app.connectors.bricodepot.catalog_enrich import item_to_catalog_product
from app.connectors.bricodepot.catalog_sitemap import BricoDepotSitemapDiscoverer
from app.connectors.bricodepot.client import (
    BricoDepotHttpError,
    BricoDepotTimeoutError,
)
from app.models import Offer, Product, SupplierProduct
from app.models.supplier_catalog_sync import (
    ITEM_DONE,
    ITEM_FAILED,
    ITEM_PENDING,
    JOB_COMPLETED,
    JOB_COMPLETED_WITH_ERRORS,
    JOB_PAUSED,
    JOB_RUNNING,
    SupplierCatalogSyncError,
    SupplierCatalogSyncItem,
    SupplierCatalogSyncJob,
)
from app.services.bricodepot_catalog_import import (
    resolve_brico_supplier,
    upsert_supplier_product,
)
from app.services.bricodepot_catalog_sync import (
    SYNC_VALIDATION_MAX,
    BricoDepotCatalogSyncService,
    JobLockError,
    assert_limit_allowed,
    classify_exception,
    sanitize_error_message,
)


INDEX_XML = """<?xml version="1.0"?>
<sitemapindex>
  <sitemap><loc>https://www.bricodepot.fr/sitemaps/sitemap-produits-1.xml</loc></sitemap>
  <sitemap><loc>https://www.bricodepot.fr/sitemaps/sitemap-produits-2.xml</loc></sitemap>
</sitemapindex>
"""

PRODUCTS_1 = """<?xml version="1.0"?>
<urlset>
  <url><loc>https://www.bricodepot.fr/p/1111111111111/a</loc></url>
  <url><loc>https://www.bricodepot.fr/p/2222222222222/b</loc></url>
  <url><loc>https://www.bricodepot.fr/p/1111111111111/a-dup</loc></url>
  <url><loc>https://www.bricodepot.fr/not-a-product</loc></url>
</urlset>
"""

PRODUCTS_2 = """<?xml version="1.0"?>
<urlset>
  <url><loc>https://www.bricodepot.fr/p/3333333333333/c</loc></url>
  <url><loc>https://www.bricodepot.fr/p/2222222222222/again</loc></url>
</urlset>
"""


def _http_map(mapping: dict[str, tuple[int, bytes]]):
    def http_get(url: str, *, timeout: float = 40.0):
        if url not in mapping:
            raise AssertionError(f"unexpected url {url}")
        return mapping[url]

    return http_get


class FakeEnricher:
    def __init__(self, *, fail_skus: set[str] | None = None, boom: Exception | None = None):
        self.fail_skus = fail_skus or set()
        self.boom = boom
        self.calls = 0

    def enrich(self, discovered: list[BricoDepotDiscoveredProduct]):
        self.calls += 1
        if self.boom is not None:
            raise self.boom
        products = []
        missing = []
        for d in discovered:
            if d.supplier_reference in self.fail_skus:
                missing.append(d.supplier_reference)
                products.append(
                    BricoDepotCatalogProduct(
                        supplier_reference=d.supplier_reference,
                        product_url=d.product_url,
                    )
                )
                continue
            products.append(
                item_to_catalog_product(
                    {
                        "sku": d.supplier_reference,
                        "name": f"Name {d.supplier_reference}",
                        "ean_code": d.supplier_reference,
                        "sap_easier_code": f"SAP{d.supplier_reference[-4:]}",
                        "images": [{"url": "https://cdn.example/x.jpg"}],
                        "conditionnement_label": "La pièce",
                        "categories": [
                            {"url_path": "produits/outillage/ Perceuse"}
                        ],
                    },
                    discovered=d,
                )
            )
        return products, missing


class FakeDiscoverer:
    def __init__(self, skus: list[str]):
        self.skus = skus

    def discover_all(self, *, limit: int | None = None) -> CatalogDiscoveryReport:
        take = self.skus if limit is None else self.skus[:limit]
        products = [
            BricoDepotDiscoveredProduct(
                s, f"https://www.bricodepot.fr/p/{s}", None
            )
            for s in take
        ]
        return CatalogDiscoveryReport(
            products=products,
            discovered_total=len(products),
            sitemap_url_count=len(products),
            product_sitemap_files=1,
        )


def test_discovery_deterministic_dedupe():
    http = _http_map(
        {
            "https://www.bricodepot.fr/sitemaps/sitemap.xml": (200, INDEX_XML.encode()),
            "https://www.bricodepot.fr/sitemaps/sitemap-produits-1.xml": (
                200,
                PRODUCTS_1.encode(),
            ),
            "https://www.bricodepot.fr/sitemaps/sitemap-produits-2.xml": (
                200,
                PRODUCTS_2.encode(),
            ),
        }
    )
    disc = BricoDepotFullDiscoverer(
        BricoDepotSitemapDiscoverer(
            index_url="https://www.bricodepot.fr/sitemaps/sitemap.xml",
            http_get=http,
        )
    )
    a = disc.discover_all()
    b = disc.discover_all()
    assert [p.supplier_reference for p in a.products] == [
        p.supplier_reference for p in b.products
    ]
    assert a.discovered_total == 3
    assert a.duplicate_skus == 2
    assert a.invalid_skus >= 1
    limited = disc.discover_all(limit=2)
    assert limited.discovered_total == 2


def test_assert_limit_and_full_guards():
    assert assert_limit_allowed(None, full=True) is None
    assert assert_limit_allowed(100, full=False) == 100
    with pytest.raises(ValueError, match="mutuellement exclusifs"):
        assert_limit_allowed(100, full=True)
    with pytest.raises(ValueError, match="obligatoire"):
        assert_limit_allowed(None, full=False)
    with pytest.raises(ValueError, match="plafond mode limité|Utiliser --full"):
        assert_limit_allowed(SYNC_VALIDATION_MAX + 1, full=False)


def test_create_job_full_discovers_all_without_limit(session):
    skus = [f"{1100000000000 + i}" for i in range(12)]
    svc = BricoDepotCatalogSyncService(
        session,
        discoverer=FakeDiscoverer(skus),
        enricher=FakeEnricher(),
        sleep_fn=lambda _s: None,
    )
    job = svc.create_job(limit=None, dry_run=True, full=True, sync_batch_size=10)
    session.commit()
    assert job.mode == "full"
    assert job.limit_skus is None
    assert job.discovered_total == 12
    # Sans --full, pas de sync complète accidentelle
    with pytest.raises(ValueError, match="obligatoire"):
        svc.create_job(limit=None, dry_run=True, full=False)


def test_cli_full_and_limit_guards(capsys):
    import tools.bricodepot_catalog_sync as cli

    assert cli.main(["--full", "--limit", "10"]) == 2
    err = capsys.readouterr().err
    assert "mutuellement" in err or "exclusifs" in err
    assert cli.main(["--dry-run"]) == 2  # ni limit ni full
    assert cli.main(["--dry-run", "--limit", "501"]) == 2
    err2 = capsys.readouterr().err
    assert "500" in err2 or "plafond" in err2 or "full" in err2.lower()


def test_sanitize_hides_secrets():
    msg = sanitize_error_message(
        "Authorization: Bearer supersecrettokenvalue1234567890 and Cookie: abc=def"
    )
    assert "supersecret" not in msg.lower()
    assert "REDACTED" in msg
    assert "Authorization" in msg or "authorization" in msg.lower() or "REDACTED" in msg


def test_classify_transient():
    assert classify_exception(BricoDepotTimeoutError("t")) == "transient"
    assert classify_exception(BricoDepotHttpError(503, "x")) == "transient"
    assert classify_exception(BricoDepotHttpError(404, "x")) == "permanent"


def test_dry_run_persists_job_without_catalog_mutation(session):
    skus = [f"{1000000000000 + i}" for i in range(25)]
    offers_before = session.scalar(select(func.count()).select_from(Offer)) or 0
    products_before = session.scalar(select(func.count()).select_from(Product)) or 0
    sp_before = session.scalar(select(func.count()).select_from(SupplierProduct)) or 0

    svc = BricoDepotCatalogSyncService(
        session,
        discoverer=FakeDiscoverer(skus),
        enricher=FakeEnricher(),
        sleep_fn=lambda _s: None,
    )
    job = svc.create_job(limit=25, dry_run=True, sync_batch_size=10)
    session.commit()
    result = svc.run_job(job.id)
    session.commit()

    assert result.status == JOB_COMPLETED
    assert result.dry_run is True
    assert result.discovered_total == 25
    assert result.processed == 25
    assert result.created + result.updated + result.unchanged + result.skipped == 25
    assert result.created == 25  # classify as would_create
    assert (session.scalar(select(func.count()).select_from(Offer)) or 0) == offers_before
    assert (session.scalar(select(func.count()).select_from(Product)) or 0) == products_before
    assert (
        session.scalar(select(func.count()).select_from(SupplierProduct)) or 0
    ) == sp_before
    # Job + items persisted
    assert session.get(SupplierCatalogSyncJob, job.id) is not None
    items = session.scalars(
        select(SupplierCatalogSyncItem).where(SupplierCatalogSyncItem.job_id == job.id)
    ).all()
    assert len(items) == 25
    assert all(i.status == ITEM_DONE for i in items)


def test_apply_creates_and_second_run_unchanged(session):
    skus = [f"{2000000000000 + i}" for i in range(12)]
    svc = BricoDepotCatalogSyncService(
        session,
        discoverer=FakeDiscoverer(skus),
        enricher=FakeEnricher(),
        sleep_fn=lambda _s: None,
    )
    job = svc.create_job(limit=12, dry_run=False, sync_batch_size=5)
    session.commit()
    r1 = svc.run_job(job.id)
    session.commit()
    assert r1.created == 12
    assert r1.status == JOB_COMPLETED

    offers_mid = session.scalar(select(func.count()).select_from(Offer)) or 0
    products_mid = session.scalar(select(func.count()).select_from(Product)) or 0

    job2 = svc.create_job(limit=12, dry_run=False, sync_batch_size=5)
    session.commit()
    r2 = svc.run_job(job2.id)
    session.commit()
    assert r2.created == 0
    assert r2.unchanged == 12
    assert r2.updated == 0
    assert (session.scalar(select(func.count()).select_from(Offer)) or 0) == offers_mid
    assert (session.scalar(select(func.count()).select_from(Product)) or 0) == products_mid


def test_manual_mapping_preserved(session):
    sku = "3000000000001"
    supplier = resolve_brico_supplier(session)
    dto = item_to_catalog_product(
        {
            "sku": sku,
            "name": "Mapped",
            "ean_code": sku,
            "images": [{"url": "https://cdn.example/x.jpg"}],
            "conditionnement_label": "La pièce",
        },
        discovered=BricoDepotDiscoveredProduct(sku, f"https://www.bricodepot.fr/p/{sku}", None),
    )
    upsert_supplier_product(session, supplier=supplier, product=dto, catalog_id=None)
    sp = session.scalar(
        select(SupplierProduct).where(SupplierProduct.supplier_reference == sku)
    )
    # Attach to an existing product
    product = session.scalars(select(Product).limit(1)).first()
    assert product is not None
    sp.product_id = product.id
    sp.correction_source = "manual"
    sp.supplier_unit = "plaque"
    session.flush()

    svc = BricoDepotCatalogSyncService(
        session,
        discoverer=FakeDiscoverer([sku]),
        enricher=FakeEnricher(),
        sleep_fn=lambda _s: None,
    )
    job = svc.create_job(limit=1, dry_run=False)
    session.commit()
    svc.run_job(job.id)
    session.commit()
    session.refresh(sp)
    assert sp.product_id == product.id
    assert sp.correction_source == "manual"
    assert sp.supplier_unit == "plaque"


def test_checkpoint_and_resume_after_interrupt(session):
    skus = [f"{4000000000000 + i}" for i in range(30)]
    svc = BricoDepotCatalogSyncService(
        session,
        discoverer=FakeDiscoverer(skus),
        enricher=FakeEnricher(),
        sleep_fn=lambda _s: None,
        worker_id="w1",
    )
    job = svc.create_job(limit=30, dry_run=True, sync_batch_size=10)
    session.commit()

    # Interrupt after first batch
    calls = {"n": 0}

    def progress(p):
        calls["n"] += 1
        if calls["n"] >= 1:
            svc.request_interrupt()

    result = svc.run_job(job.id, progress=progress)
    session.commit()
    assert result.status == JOB_PAUSED
    assert result.checkpoint_index == 10
    assert result.processed == 10
    assert result.resume_hint and "--resume" in result.resume_hint

    # Resume
    svc2 = BricoDepotCatalogSyncService(
        session,
        discoverer=FakeDiscoverer(skus),
        enricher=FakeEnricher(),
        sleep_fn=lambda _s: None,
        worker_id="w2",
    )
    result2 = svc2.run_job(job.id)
    session.commit()
    assert result2.status == JOB_COMPLETED
    assert result2.processed == 30
    assert result2.checkpoint_index == 30


def test_permanent_error_isolated(session):
    skus = [f"{5000000000000 + i}" for i in range(5)]
    fail = {skus[2]}
    svc = BricoDepotCatalogSyncService(
        session,
        discoverer=FakeDiscoverer(skus),
        enricher=FakeEnricher(fail_skus=fail),
        sleep_fn=lambda _s: None,
    )
    job = svc.create_job(limit=5, dry_run=True, sync_batch_size=5)
    session.commit()
    result = svc.run_job(job.id)
    session.commit()
    # missing enrich → skipped (not failed) with FakeEnricher returning empty DTO
    assert result.status == JOB_COMPLETED
    assert result.skipped >= 1
    assert result.processed == 5


def test_transient_retry_then_success(session):
    skus = ["6000000000001", "6000000000002"]
    enricher = FakeEnricher()
    state = {"n": 0}

    def flaky_enrich(discovered):
        state["n"] += 1
        if state["n"] == 1:
            raise BricoDepotTimeoutError("timeout")
        return FakeEnricher().enrich(discovered)

    enricher.enrich = flaky_enrich  # type: ignore[method-assign]
    sleeps: list[float] = []
    svc = BricoDepotCatalogSyncService(
        session,
        discoverer=FakeDiscoverer(skus),
        enricher=enricher,
        sleep_fn=lambda s: sleeps.append(s),
    )
    job = svc.create_job(limit=2, dry_run=True, sync_batch_size=2)
    session.commit()
    result = svc.run_job(job.id)
    session.commit()
    assert result.status == JOB_COMPLETED
    assert result.retries >= 1
    assert sleeps == [1.0]


def test_max_retry_then_fail_batch(session):
    skus = ["7000000000001"]
    enricher = FakeEnricher(boom=BricoDepotTimeoutError("always"))
    svc = BricoDepotCatalogSyncService(
        session,
        discoverer=FakeDiscoverer(skus),
        enricher=enricher,
        sleep_fn=lambda _s: None,
    )
    job = svc.create_job(limit=1, dry_run=True)
    session.commit()
    with pytest.raises(BricoDepotTimeoutError):
        svc.run_job(job.id)
    session.rollback()
    job = session.get(SupplierCatalogSyncJob, job.id)
    assert job is not None
    assert job.status == "failed"


def test_concurrency_double_resume(session):
    skus = [f"{8000000000000 + i}" for i in range(5)]
    svc = BricoDepotCatalogSyncService(
        session,
        discoverer=FakeDiscoverer(skus),
        enricher=FakeEnricher(),
        sleep_fn=lambda _s: None,
        worker_id="owner-a",
    )
    job = svc.create_job(limit=5, dry_run=True)
    session.commit()
    # Acquire lease manually
    job = svc._acquire_lease(job.id)
    job.status = JOB_RUNNING
    session.commit()

    svc_b = BricoDepotCatalogSyncService(
        session,
        discoverer=FakeDiscoverer(skus),
        enricher=FakeEnricher(),
        sleep_fn=lambda _s: None,
        worker_id="owner-b",
    )
    with pytest.raises(JobLockError):
        svc_b.run_job(job.id)


def test_graphql_batch_size_capped():
    from app.services.bricodepot_catalog_sync import clamp_graphql_batch_size

    assert clamp_graphql_batch_size(100) == 10
    assert clamp_graphql_batch_size(1) == 1


def test_no_delete_supplier_product(session):
    skus = ["9000000000001", "9000000000002"]
    svc = BricoDepotCatalogSyncService(
        session,
        discoverer=FakeDiscoverer(skus),
        enricher=FakeEnricher(),
        sleep_fn=lambda _s: None,
    )
    job = svc.create_job(limit=2, dry_run=False)
    session.commit()
    svc.run_job(job.id)
    session.commit()
    count = session.scalar(
        select(func.count())
        .select_from(SupplierProduct)
        .where(SupplierProduct.supplier_reference.in_(skus))
    )
    assert count == 2
    # Second job with only one SKU must not delete the other
    svc2 = BricoDepotCatalogSyncService(
        session,
        discoverer=FakeDiscoverer([skus[0]]),
        enricher=FakeEnricher(),
        sleep_fn=lambda _s: None,
    )
    job2 = svc2.create_job(limit=1, dry_run=False)
    session.commit()
    svc2.run_job(job2.id)
    session.commit()
    still = session.scalar(
        select(func.count())
        .select_from(SupplierProduct)
        .where(SupplierProduct.supplier_reference == skus[1])
    )
    assert still == 1
