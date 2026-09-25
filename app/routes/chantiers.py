from collections.abc import Callable
from typing import Annotated, TypeVar

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import AwareDatetime

from app.routes.api import SessionDependency
from app.schemas.approvisionnement import ApprovisionnementRead, ApprovisionnementWrite
from app.schemas.chantier import ChantierListItem, ChantierRead, ChantierUpdate, ChantierWrite
from app.schemas.shopping_list import (
    ShoppingLineTrackingRead,
    ShoppingLineTrackingWrite,
    ShoppingListRead,
)
from app.services.approvisionnement import ApprovisionnementService
from app.services.chantiers import (
    ChantierConflict,
    ChantierNotFound,
    ChantierService,
    ProductsNotFound,
)
from app.services.shopping_list import ShoppingListService

router = APIRouter(prefix="/api/chantiers", tags=["Chantiers"])
Result = TypeVar("Result")


def execute(action: Callable[[], Result]) -> Result:
    try:
        return action()
    except ChantierNotFound as error:
        raise HTTPException(404, str(error)) from error
    except ProductsNotFound as error:
        raise HTTPException(
            404, {"message": "Produits introuvables.", "product_ids": error.ids}
        ) from error
    except ChantierConflict as error:
        raise HTTPException(409, str(error)) from error


@router.get("", response_model=list[ChantierListItem])
def list_chantiers(
    session: SessionDependency, limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0)
):
    return ChantierService(session).list(limit, offset)


@router.post("", response_model=ChantierRead, status_code=201)
def create_chantier(payload: ChantierWrite, session: SessionDependency, response: Response):
    result = execute(lambda: ChantierService(session).create(payload))
    response.headers["Location"] = f"/api/chantiers/{result.id}"
    return result


@router.get("/{chantier_id}", response_model=ChantierRead)
def get_chantier(chantier_id: int, session: SessionDependency):
    return execute(lambda: ChantierService(session).get(chantier_id))


@router.put("/{chantier_id}", response_model=ChantierRead)
def update_chantier(chantier_id: int, payload: ChantierUpdate, session: SessionDependency):
    return execute(lambda: ChantierService(session).update(chantier_id, payload))


@router.delete("/{chantier_id}", status_code=204)
def delete_chantier(
    chantier_id: int,
    session: SessionDependency,
    updated_at: Annotated[AwareDatetime, Query(description="Jeton de la dernière lecture.")],
):
    execute(lambda: ChantierService(session).delete(chantier_id, updated_at))
    return Response(status_code=204)


@router.get("/{chantier_id}/liste-achat", response_model=ShoppingListRead)
def get_liste_achat(chantier_id: int, session: SessionDependency):
    return execute(lambda: ShoppingListService(session).get(chantier_id))


@router.put(
    "/{chantier_id}/liste-achat/lignes/{line_key}",
    response_model=ShoppingLineTrackingRead,
)
def put_liste_achat_ligne(
    chantier_id: int,
    line_key: str,
    payload: ShoppingLineTrackingWrite,
    session: SessionDependency,
):
    return execute(
        lambda: ShoppingListService(session).update_line(chantier_id, line_key, payload)
    )


@router.get("/{chantier_id}/approvisionnement", response_model=ApprovisionnementRead)
def get_approvisionnement(chantier_id: int, session: SessionDependency):
    return execute(lambda: ApprovisionnementService(session).get(chantier_id))


@router.put("/{chantier_id}/approvisionnement", response_model=ApprovisionnementRead)
def put_approvisionnement(
    chantier_id: int, payload: ApprovisionnementWrite, session: SessionDependency
):
    return execute(lambda: ApprovisionnementService(session).upsert(chantier_id, payload))


@router.delete("/{chantier_id}/approvisionnement", status_code=204)
def delete_approvisionnement(
    chantier_id: int,
    session: SessionDependency,
    updated_at: Annotated[AwareDatetime, Query(description="Jeton de la dernière lecture.")],
):
    execute(lambda: ApprovisionnementService(session).delete(chantier_id, updated_at))
    return Response(status_code=204)
