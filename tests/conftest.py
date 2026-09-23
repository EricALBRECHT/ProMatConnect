import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema

from app.config import Settings
from app.database import Base, make_engine
from app.main import create_app
from scripts.seed import seed


@pytest.fixture
def database_url(tmp_path: Path):
    # Toute URL externe doit cibler explicitement une base jetable de test.
    url = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        if not parsed.database or not parsed.database.endswith("_test"):
            raise ValueError("TEST_DATABASE_URL doit cibler une base finissant par _test.")
        # Un nouveau schéma par test, sans supprimer ni réinitialiser un schéma existant.
        schema = f"test_{uuid4().hex}"
        setup_engine = make_engine(url)
        try:
            with setup_engine.begin() as connection:
                connection.execute(CreateSchema(schema))
        finally:
            setup_engine.dispose()
        url = parsed.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(
            hide_password=False
        )
    return url


@pytest.fixture
def engine(database_url):
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed(session)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(engine, database_url):
    with TestClient(create_app(Settings(database_url=database_url, seed_on_start=True))) as client:
        yield client
