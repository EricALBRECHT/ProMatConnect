from app.connectors.fake_base import DatabaseFakeConnector


class FakePointPConnector(DatabaseFakeConnector):
    SUPPLIER_NAME = "POINT.P TEST"
