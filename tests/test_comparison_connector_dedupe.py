"""Déduplication enseigne Brico. La participation dépend de Supplier.active."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.connectors.bricodepot.connector import SUPPLIER_NAME, is_brico_supplier_name
from app.connectors.registry import (
    build_comparison_connectors,
    build_connectors,
    build_live_connectors,
)
from app.database import Base
from app.models import Supplier
from app.schemas.location import ResolvedOrigin


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    import app.models  # noqa: F401

    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as session:
        yield session


def _origin(citycode: str | None = "80021") -> ResolvedOrigin:
    return ResolvedOrigin(
        type="site",
        label="Amiens",
        source="geopf",
        latitude=49.89,
        longitude=2.30,
        citycode=citycode,
        city="Amiens",
        postcode="80000",
    )


def _seed_suppliers(session: Session) -> None:
    session.add_all(
        [
            Supplier(name="POINT.P TEST", source_type="file", source_key="seed"),
            Supplier(name="GEDIMAT TEST", source_type="file", source_key="seed"),
            Supplier(name="BRICO_DEPOT", source_type="file", source_key="import"),
        ]
    )
    session.commit()


def test_live_off_keeps_file_brico_and_test_suppliers(session):
    _seed_suppliers(session)
    settings = Settings(bricodepot_live_enabled=False)
    connectors = build_comparison_connectors(
        session, resolved_origin=_origin(), settings=settings
    )
    names = [c.supplier_name for c in connectors]
    assert "POINT.P TEST" in names
    assert "GEDIMAT TEST" in names
    assert "BRICO_DEPOT" in names
    assert SUPPLIER_NAME not in names
    assert build_live_connectors(session, resolved_origin=_origin(), settings=settings) == []


def test_live_on_dedupes_brico_enseigne_and_keeps_active_test(session):
    _seed_suppliers(session)
    settings = Settings(bricodepot_live_enabled=True, bricodepot_max_stores=1)
    connectors = build_comparison_connectors(
        session, resolved_origin=_origin(), settings=settings
    )
    names = [c.supplier_name for c in connectors]
    # Une seule enseigne Brico = connecteur live
    assert names.count(SUPPLIER_NAME) == 1
    assert "BRICO_DEPOT" not in names
    assert not any(is_brico_supplier_name(n) and n != SUPPLIER_NAME for n in names)
    assert "POINT.P TEST" in names
    assert "GEDIMAT TEST" in names
    # build_connectors brut inchangé (données conservées)
    raw = [c.supplier_name for c in build_connectors(session)]
    assert "BRICO_DEPOT" in raw
    assert "POINT.P TEST" in raw


def test_live_on_without_citycode_keeps_catalog(session):
    """Sans citycode → pas de live → catalogue complet (dont TEST + file Brico)."""
    _seed_suppliers(session)
    settings = Settings(bricodepot_live_enabled=True)
    connectors = build_comparison_connectors(
        session, resolved_origin=_origin(citycode=None), settings=settings
    )
    names = [c.supplier_name for c in connectors]
    assert "BRICO_DEPOT" in names
    assert "POINT.P TEST" in names
    assert SUPPLIER_NAME not in names


def test_app_js_dedupes_brico_even_when_both_invalid():
    app_js = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "Au plus une option par enseigne Brico" in app_js
    assert "bricoIndexes" in app_js
