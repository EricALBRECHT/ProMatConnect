import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Base, make_engine
from app.main import create_app
from scripts.seed import seed


@pytest.fixture
def database_url(tmp_path: Path):
    # Toute URL externe doit cibler explicitement une base jetable de test.
    url = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    if not url.startswith("sqlite") and not url.rsplit("/", 1)[-1].endswith("_test"):
        raise ValueError("TEST_DATABASE_URL doit cibler une base dont le nom finit par _test.")
    return url


@pytest.fixture
def engine(database_url):
    engine = make_engine(database_url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed(session)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(engine, database_url):
    with TestClient(create_app(Settings(database_url=database_url, seed_on_start=True))) as client:
        yield client
