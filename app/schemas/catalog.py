from pydantic import BaseModel, ConfigDict, Field


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    category: str
    subcategory: str | None = None
    reference_unit: str
    description: str | None
    attributes: dict | None = None
    is_active: bool = True
    is_legacy: bool = False


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=3, max_length=64, pattern=r"^PMC-[A-Z0-9-]+$")
    name: str = Field(min_length=2, max_length=200)
    category: str = Field(min_length=1, max_length=80)
    subcategory: str | None = Field(default=None, max_length=80)
    reference_unit: str = Field(min_length=1, max_length=30)
    description: str | None = Field(default=None, max_length=500)
    attributes: dict | None = None
