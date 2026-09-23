from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, selectinload

from app.models.chantier import Chantier, ChantierMaterial


class ChantierRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _read_statement():
        return (
            select(Chantier)
            .options(selectinload(Chantier.materiaux).selectinload(ChantierMaterial.produit))
            .execution_options(populate_existing=True)
        )

    def get(self, chantier_id: int) -> Chantier | None:
        return self.session.scalar(self._read_statement().where(Chantier.id == chantier_id))

    def list(self, limit: int, offset: int) -> list[Chantier]:
        return list(
            self.session.scalars(
                self._read_statement()
                .order_by(Chantier.updated_at.desc(), Chantier.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )

    def compare_and_update(self, chantier_id: int, expected: datetime, values: dict) -> bool:
        result = self.session.execute(
            update(Chantier)
            .where(Chantier.id == chantier_id, Chantier.updated_at == expected)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    def replace_materials(self, chantier_id: int, materials: list[dict]) -> None:
        self.session.execute(
            delete(ChantierMaterial)
            .where(ChantierMaterial.chantier_id == chantier_id)
            .execution_options(synchronize_session=False)
        )
        self.session.add_all(
            [ChantierMaterial(chantier_id=chantier_id, **line) for line in materials]
        )

    def compare_and_delete(self, chantier_id: int, expected: datetime) -> bool:
        result = self.session.execute(
            delete(Chantier)
            .where(Chantier.id == chantier_id, Chantier.updated_at == expected)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1
