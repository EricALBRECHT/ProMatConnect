from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.chantier import ApprovisionnementRetenu, utc_now
from app.repositories.chantiers import ChantierRepository
from app.schemas.approvisionnement import ApprovisionnementRead, ApprovisionnementWrite
from app.services.chantiers import ChantierConflict, ChantierNotFound


STALE_COMPARISON_MESSAGE = (
    "Le chantier a été modifié depuis cette comparaison. "
    "Relancez la comparaison avant de choisir une solution."
)


def materials_fingerprint(materiaux) -> str:
    """Empreinte stable des besoins : product_id:quantite (3 décimales), triés."""
    parts: list[str] = []
    for line in sorted(materiaux, key=lambda item: int(item.product_id)):
        quantity = Decimal(str(line.quantite)).quantize(Decimal("0.001"))
        parts.append(f"{line.product_id}:{quantity}")
    return "|".join(parts)


class ApprovisionnementService:
    def __init__(self, session: Session):
        self.session = session
        self.chantiers = ChantierRepository(session)

    def _get_row(self, chantier_id: int) -> ApprovisionnementRetenu | None:
        return self.session.scalar(
            select(ApprovisionnementRetenu).where(
                ApprovisionnementRetenu.chantier_id == chantier_id
            )
        )

    def _to_read(self, row: ApprovisionnementRetenu, chantier) -> ApprovisionnementRead:
        current = materials_fingerprint(chantier.materiaux)
        return ApprovisionnementRead(
            id=row.id,
            chantier_id=row.chantier_id,
            strategy_key=row.strategy_key,
            strategy_title=row.strategy_title,
            chosen_at=row.chosen_at,
            needs_fingerprint=row.needs_fingerprint,
            obsolete=current != row.needs_fingerprint,
            currency=row.currency,
            tax_basis=row.tax_basis,
            material_total=row.material_total,
            estimated_procurement_cost=row.estimated_procurement_cost,
            total_distance_km=row.total_distance_km,
            travel_minutes=row.travel_minutes,
            snapshot=row.snapshot,
            chantier_updated_at=chantier.updated_at,
        )

    def get(self, chantier_id: int) -> ApprovisionnementRead:
        chantier = self.chantiers.get(chantier_id)
        if chantier is None:
            raise ChantierNotFound("Chantier introuvable.")
        row = self._get_row(chantier_id)
        if row is None:
            raise ChantierNotFound("Aucun approvisionnement retenu.")
        return self._to_read(row, chantier)

    def upsert(self, chantier_id: int, payload: ApprovisionnementWrite) -> ApprovisionnementRead:
        chantier = self.chantiers.get(chantier_id)
        if chantier is None:
            raise ChantierNotFound("Chantier introuvable.")
        expected = payload.updated_at.astimezone(timezone.utc)
        current_fp = materials_fingerprint(chantier.materiaux)
        if current_fp != payload.needs_fingerprint:
            raise ChantierConflict(STALE_COMPARISON_MESSAGE)
        strategy = payload.strategy
        snapshot = {
            "strategy": strategy.model_dump(mode="json"),
            "origin": payload.origin.model_dump(mode="json") if payload.origin else None,
            "cost_parameters": (
                payload.cost_parameters.model_dump(mode="json")
                if payload.cost_parameters
                else None
            ),
            "currency": payload.currency,
            "tax_basis": payload.tax_basis,
        }
        values = {
            "updated_at": max(utc_now(), expected + timedelta(microseconds=1)),
        }
        if not self.chantiers.compare_and_update(chantier_id, expected, values):
            self.session.rollback()
            raise ChantierConflict(
                "Ce chantier a été modifié ou supprimé. Rechargez avant d'enregistrer."
            )
        row = self._get_row(chantier_id)
        chosen_at = utc_now()
        fields = dict(
            strategy_key=strategy.key,
            strategy_title=strategy.title,
            chosen_at=chosen_at,
            needs_fingerprint=payload.needs_fingerprint,
            currency=payload.currency,
            tax_basis=payload.tax_basis,
            material_total=strategy.material_total,
            estimated_procurement_cost=strategy.estimated_procurement_cost,
            total_distance_km=strategy.total_distance_km,
            travel_minutes=strategy.travel_minutes,
            snapshot=snapshot,
        )
        if row is None:
            row = ApprovisionnementRetenu(chantier_id=chantier_id, **fields)
            self.session.add(row)
        else:
            for key, value in fields.items():
                setattr(row, key, value)
        self.session.flush()
        refreshed = self.chantiers.get(chantier_id)
        assert refreshed is not None
        result = self._to_read(row, refreshed)
        self.session.commit()
        return result

    def delete(self, chantier_id: int, expected: datetime) -> None:
        chantier = self.chantiers.get(chantier_id)
        if chantier is None:
            raise ChantierNotFound("Chantier introuvable.")
        row = self._get_row(chantier_id)
        if row is None:
            raise ChantierNotFound("Aucun approvisionnement retenu.")
        token = expected.astimezone(timezone.utc)
        values = {"updated_at": max(utc_now(), token + timedelta(microseconds=1))}
        if not self.chantiers.compare_and_update(chantier_id, token, values):
            self.session.rollback()
            raise ChantierConflict(
                "Ce chantier a été modifié ou supprimé. Rechargez avant de supprimer."
            )
        self.session.execute(
            delete(ApprovisionnementRetenu).where(
                ApprovisionnementRetenu.chantier_id == chantier_id
            )
        )
        self.session.commit()
