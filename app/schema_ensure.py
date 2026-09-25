"""Migrations additives pour volumes PostgreSQL existants (create_all n'altère pas les colonnes)."""

from __future__ import annotations

from sqlalchemy import Engine, text


_STATEMENTS = (
    "ALTER TABLE products ALTER COLUMN code TYPE VARCHAR(64)",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS subcategory VARCHAR(80)",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS attributes JSONB",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS is_legacy BOOLEAN DEFAULT FALSE",
    "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS source_type VARCHAR(20) DEFAULT 'demo'",
    "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS source_key VARCHAR(80)",
    "ALTER TABLE agencies ADD COLUMN IF NOT EXISTS external_id VARCHAR(80)",
    "ALTER TABLE agencies ALTER COLUMN latitude DROP NOT NULL",
    "ALTER TABLE agencies ALTER COLUMN longitude DROP NOT NULL",
    "ALTER TABLE supplier_products ALTER COLUMN product_id DROP NOT NULL",
    "ALTER TABLE supplier_products ADD COLUMN IF NOT EXISTS brand VARCHAR(80)",
    "ALTER TABLE supplier_products ADD COLUMN IF NOT EXISTS ean VARCHAR(32)",
    "ALTER TABLE supplier_products ADD COLUMN IF NOT EXISTS packaging_quantity NUMERIC(12,3) DEFAULT 1",
    "ALTER TABLE supplier_products ADD COLUMN IF NOT EXISTS reference_unit VARCHAR(30)",
    "ALTER TABLE supplier_products ADD COLUMN IF NOT EXISTS image_url VARCHAR(500)",
    "ALTER TABLE supplier_products ADD COLUMN IF NOT EXISTS introduced_by_catalog_id INTEGER",
    "ALTER TABLE supplier_products ADD COLUMN IF NOT EXISTS correction_source VARCHAR(20)",
    "ALTER TABLE offers ADD COLUMN IF NOT EXISTS currency VARCHAR(3) DEFAULT 'EUR'",
    "ALTER TABLE offers ADD COLUMN IF NOT EXISTS tax_basis VARCHAR(8) DEFAULT 'HT'",
    "ALTER TABLE offers ADD COLUMN IF NOT EXISTS source_type VARCHAR(20) DEFAULT 'demo'",
    "ALTER TABLE offers ADD COLUMN IF NOT EXISTS source_key VARCHAR(80)",
    "ALTER TABLE offers ADD COLUMN IF NOT EXISTS source_url VARCHAR(500)",
    "ALTER TABLE offers ADD COLUMN IF NOT EXISTS seller VARCHAR(120)",
    "ALTER TABLE offers ADD COLUMN IF NOT EXISTS verification_status VARCHAR(40)",
    "ALTER TABLE offers ADD COLUMN IF NOT EXISTS observed_at TIMESTAMPTZ",
    "ALTER TABLE offers ADD COLUMN IF NOT EXISTS catalog_id INTEGER",
    "ALTER TABLE offers ADD COLUMN IF NOT EXISTS vat_rate NUMERIC(5,2)",
    "ALTER TABLE supplier_imports ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE",
    "ALTER TABLE supplier_imports ADD COLUMN IF NOT EXISTS tax_basis VARCHAR(8)",
    "ALTER TABLE supplier_imports ADD COLUMN IF NOT EXISTS rows_with_price INTEGER DEFAULT 0",
    "ALTER TABLE supplier_imports ADD COLUMN IF NOT EXISTS rows_without_price INTEGER DEFAULT 0",
)


def ensure_schema(engine: Engine) -> None:
    """Applique uniquement des ALTER ADD / DROP NOT NULL — aucun DROP de table."""
    dialect = engine.dialect.name
    if dialect == "sqlite":
        _ensure_sqlite(engine)
        return
    if dialect != "postgresql":
        return
    with engine.begin() as connection:
        for statement in _STATEMENTS:
            connection.execute(text(statement))
        connection.execute(
            text(
                """
                DO $$ BEGIN
                  ALTER TABLE agencies
                    ADD CONSTRAINT uq_agencies_supplier_external
                    UNIQUE (supplier_id, external_id);
                EXCEPTION WHEN duplicate_table OR duplicate_object THEN NULL;
                END $$;
                """
            )
        )
        # Backfill catalog_id depuis source_key quand possible (additif, non destructif).
        connection.execute(
            text(
                """
                UPDATE offers o
                SET catalog_id = si.id
                FROM supplier_imports si
                WHERE o.catalog_id IS NULL
                  AND o.source_key IS NOT NULL
                  AND o.source_key = si.source_key
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE supplier_products
                SET packaging_quantity = 1
                WHERE packaging_quantity IS NULL
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE products
                SET is_active = TRUE
                WHERE is_active IS NULL
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE products
                SET is_legacy = TRUE
                WHERE code ~ '^PMC[0-9]{4}$'
                  AND (is_legacy IS NULL OR is_legacy = FALSE)
                  AND code NOT LIKE 'PMC-%'
                """
            )
        )


def _ensure_sqlite(engine: Engine) -> None:
    """SQLite de test : ADD COLUMN si absent."""
    with engine.begin() as connection:
        tables = {
            "products": (
                ("subcategory", "VARCHAR(80)"),
                ("attributes", "JSON"),
                ("is_active", "BOOLEAN DEFAULT 1"),
                ("is_legacy", "BOOLEAN DEFAULT 0"),
            ),
            "suppliers": (("source_type", "VARCHAR(20) DEFAULT 'demo'"), ("source_key", "VARCHAR(80)")),
            "agencies": (("external_id", "VARCHAR(80)"),),
            "supplier_products": (
                ("brand", "VARCHAR(80)"),
                ("ean", "VARCHAR(32)"),
                ("packaging_quantity", "NUMERIC(12,3) DEFAULT 1"),
                ("reference_unit", "VARCHAR(30)"),
                ("image_url", "VARCHAR(500)"),
                ("introduced_by_catalog_id", "INTEGER"),
                ("correction_source", "VARCHAR(20)"),
            ),
            "offers": (
                ("currency", "VARCHAR(3) DEFAULT 'EUR'"),
                ("tax_basis", "VARCHAR(8) DEFAULT 'HT'"),
                ("source_type", "VARCHAR(20) DEFAULT 'demo'"),
                ("source_key", "VARCHAR(80)"),
                ("source_url", "VARCHAR(500)"),
                ("seller", "VARCHAR(120)"),
                ("verification_status", "VARCHAR(40)"),
                ("observed_at", "DATETIME"),
                ("catalog_id", "INTEGER"),
                ("vat_rate", "NUMERIC(5,2)"),
            ),
            "supplier_imports": (
                ("active", "BOOLEAN DEFAULT 1"),
                ("tax_basis", "VARCHAR(8)"),
                ("rows_with_price", "INTEGER DEFAULT 0"),
                ("rows_without_price", "INTEGER DEFAULT 0"),
            ),
        }
        for table, columns in tables.items():
            existing = {
                row[1] for row in connection.execute(text(f"PRAGMA table_info({table})"))
            }
            # Table may not exist yet on brand-new sqlite before create_all order.
            if not existing and table == "supplier_imports":
                continue
            for name, ddl in columns:
                if name not in existing and existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
