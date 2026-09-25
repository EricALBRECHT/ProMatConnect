from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import delete, event, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Agency, Offer, Product, Supplier, SupplierProduct
from app.models.chantier import Chantier, ChantierMaterial
from app.repositories.chantiers import ChantierRepository
from app.schemas.chantier import ChantierUpdate, ChantierWrite
from app.services.chantiers import ChantierConflict, ChantierService


def payload(**changes):
    return {"nom": "Rénovation Dupont", "adresse": "10 rue du Chantier, Paris", **changes}


def create(http_client, **changes):
    response = http_client.post("/api/chantiers", json=payload(**changes))
    assert response.status_code == 201, response.text
    return response.json()


def edit(data, **changes):
    values = {
        key: data[key]
        for key in (
            "nom",
            "client",
            "adresse",
            "latitude",
            "longitude",
            "date_prevue",
            "notes",
            "updated_at",
        )
    }
    values["materiaux"] = [
        {key: m[key] for key in ("product_id", "quantite", "ordre")} for m in data["materiaux"]
    ]
    return {**values, **changes}


def test_create_full_chantier_and_read(client):
    data = create(
        client,
        client="Mme Dupont",
        notes="Étage 2",
        date_prevue="2027-01-15",
        latitude="48.856600",
        longitude="2.352200",
        materiaux=[
            {"product_id": 1, "quantite": "30", "ordre": 1},
            {"product_id": 2, "quantite": "10.125", "ordre": 0},
        ],
    )
    assert data["nom"] == "Rénovation Dupont" and data["client"] == "Mme Dupont"
    assert data["date_prevue"] == "2027-01-15" and data["notes"] == "Étage 2"
    assert Decimal(data["latitude"]) == Decimal("48.856600")
    assert Decimal(data["longitude"]) == Decimal("2.352200")
    assert [m["product_id"] for m in data["materiaux"]] == [2, 1]
    assert Decimal(data["materiaux"][0]["quantite"]) == Decimal("10.125")
    assert data["materiaux"][1]["unite"] == "plaque"
    assert data["materiaux"][1]["product_code"] == "PMC0001"
    assert data["materiaux"][1]["product_name"]
    assert all(m["chantier_id"] == data["id"] for m in data["materiaux"])
    assert data["created_at"].endswith("Z") and data["updated_at"].endswith("Z")
    assert client.get(f"/api/chantiers/{data['id']}").json() == data


@pytest.mark.parametrize("materials", [None, []])
def test_create_empty_chantier(client, materials):
    extra = {} if materials is None else {"materiaux": materials}
    data = create(client, **extra)
    assert data["materiaux"] == []
    assert data["latitude"] is data["longitude"] is None
    assert data["client"] is data["notes"] is data["date_prevue"] is None
    assert client.post("/api/compare", json={"lines": []}).status_code == 422


def test_list_chantiers_paginated(client):
    assert client.get("/api/chantiers").json() == []
    first = create(client)
    second = create(client, nom="Second")
    data = client.get("/api/chantiers").json()
    assert [c["id"] for c in data] == [second["id"], first["id"]]
    assert all("materiaux_count" in item for item in data)
    assert all(item["approvisionnement_status"] == "none" for item in data)
    assert client.get("/api/chantiers?limit=1&offset=1").json() == [
        {
            "id": first["id"],
            "nom": first["nom"],
            "client": first["client"],
            "adresse": first["adresse"],
            "date_prevue": first["date_prevue"],
            "created_at": first["created_at"],
            "updated_at": first["updated_at"],
            "materiaux_count": len(first["materiaux"]),
            "approvisionnement_status": "none",
            "material_total": None,
        }
    ]
    assert client.get("/api/chantiers?limit=0").status_code == 422


def test_update_metadata_and_lines_then_empty(client):
    before = create(client, materiaux=[{"product_id": 1, "quantite": "30"}])
    response = client.put(
        f"/api/chantiers/{before['id']}",
        json=edit(before, nom="Modifié", materiaux=[{"product_id": 7, "quantite": "150.125"}]),
    )
    assert response.status_code == 200, response.text
    after = response.json()
    assert after["created_at"] == before["created_at"]
    assert after["updated_at"] > before["updated_at"]
    assert after["nom"] == "Modifié" and len(after["materiaux"]) == 1
    assert after["materiaux"][0]["unite"] == "vis"
    assert Decimal(after["materiaux"][0]["quantite"]) == Decimal("150.125")
    empty = client.put(f"/api/chantiers/{before['id']}", json=edit(after, materiaux=[]))
    assert empty.status_code == 200 and empty.json()["materiaux"] == []


def test_line_only_change_updates_parent_timestamp(client):
    before = create(client, materiaux=[{"product_id": 1, "quantite": "1"}])
    after = client.put(
        f"/api/chantiers/{before['id']}",
        json=edit(before, materiaux=[{"product_id": 1, "quantite": "1.001"}]),
    ).json()
    assert after["updated_at"] > before["updated_at"]
    assert Decimal(after["materiaux"][0]["quantite"]) == Decimal("1.001")


def test_delete_cascades_only_target_lines(client, session):
    target = create(client, materiaux=[{"product_id": 1, "quantite": "1"}])
    other = create(client, nom="À conserver", materiaux=[{"product_id": 1, "quantite": "2"}])
    response = client.delete(
        f"/api/chantiers/{target['id']}", params={"updated_at": target["updated_at"]}
    )
    assert response.status_code == 204 and response.content == b""
    assert client.get(f"/api/chantiers/{target['id']}").status_code == 404
    assert client.get(f"/api/chantiers/{other['id']}").json() == other
    assert (
        session.scalar(
            select(func.count())
            .select_from(ChantierMaterial)
            .where(ChantierMaterial.chantier_id == target["id"])
        )
        == 0
    )
    assert session.get(Product, 1) is not None


def test_database_cascade_and_product_restrict(session):
    # Produit sans référence fournisseur : c'est bien la nouvelle FK qui doit le protéger.
    product = Product(code="PMCCHANT", name="Test", category="Test", reference_unit="m")
    session.add(product)
    session.flush()
    chantier = Chantier(
        nom="Test",
        adresse="Adresse",
        materiaux=[ChantierMaterial(product_id=product.id, quantite=Decimal("1.25"), ordre=0)],
    )
    session.add(chantier)
    session.commit()
    product_id, chantier_id = product.id, chantier.id
    with pytest.raises(IntegrityError):
        session.execute(delete(Product).where(Product.id == product_id))
        session.commit()
    session.rollback()
    assert session.get(Product, product_id) is not None
    # DELETE SQL direct démontre la cascade base, sans cascade ORM.
    session.execute(delete(Chantier).where(Chantier.id == chantier_id))
    session.commit()
    assert session.scalar(select(func.count()).select_from(ChantierMaterial)) == 0
    assert session.get(Product, product_id) is not None


def test_unit_is_derived_without_catalog_duplication(client, session):
    data = create(client, materiaux=[{"product_id": 7, "quantite": "150"}])
    assert data["materiaux"][0]["unite"] == session.get(Product, 7).reference_unit
    assert "unite" not in ChantierMaterial.__table__.columns
    assert set(ChantierMaterial.__table__.columns.keys()) == {
        "id",
        "chantier_id",
        "product_id",
        "quantite",
        "ordre",
    }


@pytest.mark.parametrize("action", ["create", "update"])
def test_unknown_product_is_atomic(client, action):
    data = create(client, materiaux=[{"product_id": 1, "quantite": "1"}])
    materials = [{"product_id": 1, "quantite": "2"}, {"product_id": 999999, "quantite": "1"}]
    response = (
        client.post("/api/chantiers", json=payload(materiaux=materials))
        if action == "create"
        else client.put(
            f"/api/chantiers/{data['id']}",
            json=edit(data, nom="Ne pas appliquer", materiaux=materials),
        )
    )
    assert response.status_code == 404
    assert response.json()["detail"]["product_ids"] == [999999]
    listed = client.get("/api/chantiers").json()
    assert len(listed) == 1
    assert listed[0]["id"] == data["id"]
    assert listed[0]["materiaux_count"] == 1
    assert listed[0]["approvisionnement_status"] == "none"


@pytest.mark.parametrize(
    "change",
    [
        {"nom": " "},
        {"adresse": " "},
        {"latitude": "48"},
        {"latitude": "91", "longitude": "2"},
        {"latitude": "48", "longitude": "181"},
        {"latitude": "NaN", "longitude": "2"},
        {"date_prevue": "invalid"},
        {"materiaux": [{"product_id": 1, "quantite": "0"}]},
        {"materiaux": [{"product_id": 1, "quantite": "-1"}]},
        {"materiaux": [{"product_id": 1, "quantite": "1000001"}]},
        {"materiaux": [{"product_id": 1, "quantite": "0.0001"}]},
        {"materiaux": [{"product_id": 1, "quantite": "NaN"}]},
        {"materiaux": [{"product_id": 1, "quantite": "1", "unite": "lot"}]},
        {"materiaux": [{"product_id": 1, "quantite": "1"}] * 2},
        {"materiaux": [{"product_id": 1, "quantite": "1", "ordre": -1}]},
        {"materiaux": [{"product_id": i, "quantite": "1", "ordre": 0} for i in (1, 2)]},
        {"materiaux": [{"product_id": i, "quantite": "1"} for i in range(1, 102)]},
    ],
)
def test_invalid_chantier_rejected_without_write(client, change):
    assert client.post("/api/chantiers", json=payload(**change)).status_code == 422
    assert client.get("/api/chantiers").json() == []


@pytest.mark.parametrize("method", ["put", "delete"])
def test_stale_timestamp_returns_conflict(client, method):
    old = create(client)
    newer = client.put(f"/api/chantiers/{old['id']}", json=edit(old, nom="Version récente")).json()
    if method == "put":
        response = client.put(f"/api/chantiers/{old['id']}", json=edit(old, nom="Version périmée"))
    else:
        response = client.delete(
            f"/api/chantiers/{old['id']}", params={"updated_at": old["updated_at"]}
        )
    assert response.status_code == 409
    assert "Rechargez" in response.json()["detail"]
    assert client.get(f"/api/chantiers/{old['id']}").json() == newer


def test_version_required_and_put_collection_explicit(client):
    data = create(client)
    body = edit(data)
    del body["updated_at"]
    assert client.put(f"/api/chantiers/{data['id']}", json=body).status_code == 422
    body = edit(data)
    del body["materiaux"]
    assert client.put(f"/api/chantiers/{data['id']}", json=body).status_code == 422
    assert client.delete(f"/api/chantiers/{data['id']}").status_code == 422
    assert (
        client.put(
            f"/api/chantiers/{data['id']}", json=edit(data, updated_at="2026-01-01T12:00:00")
        ).status_code
        == 422
    )


def test_equivalent_timezone_and_monotonic_microseconds(client, monkeypatch):
    data = create(client)
    moment = datetime.fromisoformat(data["updated_at"])
    monkeypatch.setattr("app.services.chantiers.utc_now", lambda: moment)
    different_zone = moment.astimezone(timezone(timedelta(hours=2))).isoformat()
    after = client.put(
        f"/api/chantiers/{data['id']}", json=edit(data, updated_at=different_zone)
    ).json()
    assert datetime.fromisoformat(after["updated_at"]) == moment + timedelta(microseconds=1)
    again = client.put(f"/api/chantiers/{data['id']}", json=edit(after)).json()
    assert datetime.fromisoformat(again["updated_at"]) == moment + timedelta(microseconds=2)


def test_missing_chantier(client):
    assert client.get("/api/chantiers/99999").status_code == 404
    body = payload(updated_at="2026-01-01T12:00:00Z", materiaux=[])
    assert client.put("/api/chantiers/99999", json=body).status_code == 404
    assert (
        client.delete("/api/chantiers/99999", params={"updated_at": body["updated_at"]}).status_code
        == 404
    )


def test_two_concurrent_writers_only_one_wins(engine, monkeypatch):
    with Session(engine) as session:
        initial = ChantierService(session).create(ChantierWrite(**payload()))
    rendezvous = Barrier(2)
    original = ChantierRepository.compare_and_update

    def synchronized(self, *args, **kwargs):
        rendezvous.wait(timeout=10)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ChantierRepository, "compare_and_update", synchronized)

    def writer(name):
        with Session(engine) as session:
            try:
                result = ChantierService(session).update(
                    initial.id,
                    ChantierUpdate(
                        **payload(nom=name), updated_at=initial.updated_at, materiaux=[]
                    ),
                )
                return (200, result.nom)
            except ChantierConflict:
                return (409, name)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(writer, ["Onglet A", "Onglet B"]))
    assert sorted(status for status, _ in outcomes) == [200, 409]
    with Session(engine) as session:
        assert ChantierService(session).get(initial.id).nom == next(
            name for status, name in outcomes if status == 200
        )


def test_failure_after_parent_update_rolls_back_everything(client, monkeypatch):
    data = create(client, materiaux=[{"product_id": 1, "quantite": "1"}])

    def fail(self, chantier_id, materials):
        raise IntegrityError("test", {}, Exception("concurrent foreign key change"))

    monkeypatch.setattr(ChantierRepository, "replace_materials", fail)
    response = client.put(f"/api/chantiers/{data['id']}", json=edit(data, nom="Ne pas conserver"))
    assert response.status_code == 409
    assert client.get(f"/api/chantiers/{data['id']}").json() == data


def test_create_all_is_additive_on_preexisting_schema(database_url):
    from app.database import make_engine
    from scripts.seed import seed

    engine = make_engine(database_url)
    old_models = [Product, Supplier, Agency, SupplierProduct, Offer]
    try:
        Base.metadata.create_all(engine, tables=[m.__table__ for m in old_models])
        with Session(engine) as session:
            seed(session)
            session.get(Product, 1).description = "À conserver absolument"
            session.get(Offer, 1).price = Decimal("123.45")
            session.commit()

        def snapshot():
            with engine.connect() as connection:
                return {
                    m.__tablename__: list(
                        connection.execute(select(m.__table__).order_by(m.id)).tuples()
                    )
                    for m in old_models
                }

        before = snapshot()
        statements = []

        def capture(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        Base.metadata.create_all(engine)
        Base.metadata.create_all(engine)
        event.remove(engine, "before_cursor_execute", capture)
        assert snapshot() == before
        assert {"chantiers", "chantier_materials", "approvisionnements_retenus"} <= set(
            inspect(engine).get_table_names()
        )
        ddl = [
            s.strip().upper()
            for s in statements
            if s.strip().upper().startswith(("CREATE TABLE", "ALTER", "DROP"))
        ]
        assert len(ddl) == 3
        assert all(s.startswith("CREATE TABLE") for s in ddl)
    finally:
        engine.dispose()


def test_crud_preserves_all_catalog_data(client, session):
    models = [Product, Supplier, Agency, SupplierProduct, Offer]

    def snapshot():
        return {
            m.__tablename__: list(session.execute(select(m.__table__).order_by(m.id)).tuples())
            for m in models
        }

    before = snapshot()
    data = create(client, materiaux=[{"product_id": 1, "quantite": "20"}])
    new = client.put(f"/api/chantiers/{data['id']}", json=edit(data, materiaux=[])).json()
    assert (
        client.delete(
            f"/api/chantiers/{new['id']}", params={"updated_at": new["updated_at"]}
        ).status_code
        == 204
    )
    assert snapshot() == before


def test_swagger_has_crud_and_read_only_unit(client):
    schema = client.get("/openapi.json").json()
    assert {"get", "post"} <= schema["paths"]["/api/chantiers"].keys()
    assert {"get", "put", "delete"} <= schema["paths"]["/api/chantiers/{chantier_id}"].keys()
    assert "unite" not in schema["components"]["schemas"]["ChantierMaterialWrite"]["properties"]
