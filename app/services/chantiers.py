from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.chantier import Chantier, ChantierMaterial, utc_now
from app.repositories.catalog import CatalogRepository
from app.repositories.chantiers import ChantierRepository
from app.schemas.chantier import ChantierListItem, ChantierRead, ChantierUpdate, ChantierWrite


class ChantierNotFound(ValueError):
    pass


class ProductsNotFound(ValueError):
    def __init__(self, ids: list[int]):
        self.ids = ids


class ChantierConflict(ValueError):
    pass


class ChantierService:
    def __init__(self, session: Session):
        self.session = session
        self.repository = ChantierRepository(session)

    def get(self, chantier_id: int) -> ChantierRead:
        chantier = self.repository.get(chantier_id)
        if chantier is None:
            raise ChantierNotFound("Chantier introuvable.")
        return ChantierRead.model_validate(chantier)

    @staticmethod
    def _to_list_item(chantier: Chantier) -> ChantierListItem:
        # Import local pour éviter le cycle chantiers ↔ approvisionnement.
        from app.services.approvisionnement import materials_fingerprint

        status = "none"
        material_total = None
        appro = chantier.approvisionnement
        if appro is not None:
            current = materials_fingerprint(chantier.materiaux)
            status = "obsolete" if current != appro.needs_fingerprint else "retained"
            material_total = appro.material_total
        return ChantierListItem(
            id=chantier.id,
            nom=chantier.nom,
            client=chantier.client,
            adresse=chantier.adresse,
            date_prevue=chantier.date_prevue,
            created_at=chantier.created_at,
            updated_at=chantier.updated_at,
            materiaux_count=len(chantier.materiaux),
            approvisionnement_status=status,
            material_total=material_total,
        )

    def list(self, limit: int, offset: int) -> list[ChantierListItem]:
        return [self._to_list_item(c) for c in self.repository.list(limit, offset)]

    def _validate_products(self, payload: ChantierWrite) -> None:
        ids = {line.product_id for line in payload.materiaux}
        existing = {p.id for p in CatalogRepository(self.session).get_many(list(ids))}
        if missing := sorted(ids - existing):
            raise ProductsNotFound(missing)

    @staticmethod
    def _materials(payload: ChantierWrite) -> list[dict]:
        return [
            dict(
                product_id=line.product_id,
                quantite=line.quantite,
                ordre=line.ordre if line.ordre is not None else i,
            )
            for i, line in enumerate(payload.materiaux)
        ]

    def create(self, payload: ChantierWrite) -> ChantierRead:
        try:
            self._validate_products(payload)
            chantier = Chantier(**payload.model_dump(exclude={"materiaux"}))
            chantier.materiaux = [ChantierMaterial(**line) for line in self._materials(payload)]
            self.session.add(chantier)
            self.session.flush()
            result = self.get(chantier.id)
            self.session.commit()
            return result
        except IntegrityError as error:
            self.session.rollback()
            raise ChantierConflict(
                "Données liées modifiées pendant l'enregistrement. Rechargez."
            ) from error

    def update(self, chantier_id: int, payload: ChantierUpdate) -> ChantierRead:
        try:
            self.get(chantier_id)
            self._validate_products(payload)
            expected = payload.updated_at.astimezone(timezone.utc)
            values = payload.model_dump(exclude={"materiaux", "updated_at"})
            # Jeton strictement croissant même si deux écritures arrivent à la même microseconde.
            values["updated_at"] = max(utc_now(), expected + timedelta(microseconds=1))
            if not self.repository.compare_and_update(chantier_id, expected, values):
                self.session.rollback()
                raise ChantierConflict(
                    "Ce chantier a été modifié ou supprimé. Rechargez avant d'enregistrer."
                )
            self.repository.replace_materials(chantier_id, self._materials(payload))
            self.session.flush()
            result = self.get(chantier_id)
            self.session.commit()
            return result
        except IntegrityError as error:
            self.session.rollback()
            raise ChantierConflict(
                "Données liées modifiées pendant l'enregistrement. Rechargez."
            ) from error

    def delete(self, chantier_id: int, expected: datetime) -> None:
        self.get(chantier_id)
        if not self.repository.compare_and_delete(chantier_id, expected.astimezone(timezone.utc)):
            self.session.rollback()
            raise ChantierConflict(
                "Ce chantier a été modifié ou supprimé. Rechargez avant de supprimer."
            )
        self.session.commit()
