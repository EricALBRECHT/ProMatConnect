from app.connectors.fake_base import DatabaseFakeConnector


class FakePointPConnector(DatabaseFakeConnector):
    @property
    def supplier_name(self) -> str:
        return "POINT.P TEST"
