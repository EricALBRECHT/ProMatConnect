from app.connectors.fake_base import DatabaseFakeConnector


class FakeGedimatConnector(DatabaseFakeConnector):
    SUPPLIER_NAME = "GEDIMAT TEST"
