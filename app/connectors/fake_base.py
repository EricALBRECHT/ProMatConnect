from app.connectors.demo import DemoSupplierConnector


class DatabaseFakeConnector(DemoSupplierConnector):
    """Alias historique : simulateur local alimenté par le seed de démonstration."""

    SUPPLIER_NAME: str = ""

    def __init__(self, repository):
        super().__init__(
            repository,
            self.SUPPLIER_NAME,
            source_type="demo",
            connector_prefix="demo",
        )
