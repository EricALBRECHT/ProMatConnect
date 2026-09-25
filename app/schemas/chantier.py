from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.schemas.comparison import CartLine

ApprovisionnementStatus = Literal["none", "retained", "obsolete"]


class ChantierMaterialWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int = Field(gt=0)
    quantite: Decimal
    ordre: int | None = Field(default=None, ge=0, le=2147483647)

    @field_validator("quantite")
    @classmethod
    def validate_requested_quantity(cls, value: Decimal) -> Decimal:
        # Les règles de quantité restent celles du panier PMC, sans les conditionnements.
        return CartLine(product_id=1, quantity=value).quantity


class ChantierWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    nom: str = Field(min_length=1, max_length=200)
    client: str | None = Field(default=None, max_length=200)
    adresse: str = Field(min_length=1, max_length=300)
    latitude: Decimal | None = Field(default=None, ge=-90, le=90, decimal_places=6)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180, decimal_places=6)
    date_prevue: date | None = None
    notes: str | None = Field(default=None, max_length=10000)
    materiaux: list[ChantierMaterialWrite] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_coordinates_and_lines(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Latitude et longitude doivent être présentes ensemble ou absentes.")
        ids = [line.product_id for line in self.materiaux]
        if len(ids) != len(set(ids)):
            raise ValueError("Un produit ne peut apparaître qu'une fois dans un chantier.")
        orders = [
            line.ordre if line.ordre is not None else i for i, line in enumerate(self.materiaux)
        ]
        if len(orders) != len(set(orders)):
            raise ValueError("Les ordres des lignes doivent être distincts.")
        return self


class ChantierUpdate(ChantierWrite):
    updated_at: AwareDatetime = Field(description="Jeton exact retourné par la dernière lecture.")
    # PUT remplace la collection : son omission est une erreur, pas une suppression implicite.
    materiaux: list[ChantierMaterialWrite] = Field(max_length=100)


class ChantierMaterialRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    chantier_id: int
    product_id: int
    product_name: str
    product_code: str
    product_category: str
    quantite: Decimal
    unite: str
    ordre: int


class ChantierRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    nom: str
    client: str | None
    adresse: str
    latitude: Decimal | None
    longitude: Decimal | None
    date_prevue: date | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    materiaux: list[ChantierMaterialRead]

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        # SQLite perd le fuseau de DateTime ; les écritures applicatives sont toutes UTC.
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )


class ChantierListItem(BaseModel):
    """Résumé tableau de bord pour GET /api/chantiers (sans lignes matériaux)."""

    model_config = ConfigDict(extra="forbid")
    id: int
    nom: str
    client: str | None
    adresse: str
    date_prevue: date | None
    created_at: datetime
    updated_at: datetime
    materiaux_count: int = Field(ge=0)
    approvisionnement_status: ApprovisionnementStatus
    material_total: Decimal | None = None

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )
