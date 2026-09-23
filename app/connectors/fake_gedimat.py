from app.connectors.fake_base import DatabaseFakeConnector


class FakeGedimatConnector(DatabaseFakeConnector):
    @property
    def supplier_name(self) -> str:
        return "GEDIMAT TEST"
