from sqlalchemy.orm import Session

from app.connectors.base import SupplierConnector
from app.connectors.fake_gedimat import FakeGedimatConnector
from app.connectors.fake_pointp import FakePointPConnector
from app.repositories.offers import OfferRepository


def build_connectors(session: Session) -> list[SupplierConnector]:
    repository = OfferRepository(session)
    return [FakePointPConnector(repository), FakeGedimatConnector(repository)]
