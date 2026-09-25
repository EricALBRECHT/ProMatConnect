"""Migrations additives pour volumes PostgreSQL existants (create_all n'altère pas les colonnes)."""

from __future__ import annotations

from sqlalchemy import Engine, text


_STATEMENTS = (
    "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS source_type VARCHAR(20) DEFAULT 'demo'",
    "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS source_key VARCHAR(80)",
    "ALTER TABLE agencies ADD COLUMN IF NOT EXISTS external_id VARCHAR(80)",
    "ALTER TABLE agencies ALTER COLUMN latitude DROP NOT NULL",
    "ALTER TABLE agencies ALTER COLUMN longitude DROP NOT NULL",
    "ALTER TABLE supplier_products ALTER COLUMN product_id DROP NOT NULL",
    "ALTER TABLE supplier_products ADD COLUMN IF NOT EXISTS brand VARCHAR(80)",
    "ALTER TABLE supplier_products ADD COLUMN IF NOT EXISTS ean VARCHAR(32)",
    "ALTER TABLE offers ADD COLUMN IF NOT EXISTS currency VARCHAR(3) DEFAULT 'EUR'",
    "ALTER TABLE offers ADD COLUMN IF NOT EXISTS tax_basis VARCHAR(8) DEFAULT 'HT'",
    "ALTER TABLE offers ADD COLUMN IF NOT EXISTS source_type VARCHAR(20) DEFAULT 'demo'",
    "ALTER TABLE offers ADD COLUMN IF NOT EXISTS source_key VARCHAR(80)",
    "ALTER TABLE offers ADD COLUMN IF NOT EXISTS observed_at TIMESTAMPTZ",
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
        # Index unique (supplier_id, external_id) — ignore si déjà présent.
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


def _ensure_sqlite(engine: Engine) -> None:
    """SQLite de test : ADD COLUMN si absent (pas de IF NOT EXISTS avant 3.35 partout)."""
    with engine.begin() as connection:
        tables = {
            "suppliers": (("source_type", "VARCHAR(20) DEFAULT 'demo'"), ("source_key", "VARCHAR(80)")),
            "agencies": (("external_id", "VARCHAR(80)"),),
            "supplier_products": (("brand", "VARCHAR(80)"), ("ean", "VARCHAR(32)")),
            "offers": (
                ("currency", "VARCHAR(3) DEFAULT 'EUR'"),
                ("tax_basis", "VARCHAR(8) DEFAULT 'HT'"),
                ("source_type", "VARCHAR(20) DEFAULT 'demo'"),
                ("source_key", "VARCHAR(80)"),
                ("observed_at", "DATETIME"),
            ),
        }
        for table, columns in tables.items():
            existing = {
                row[1] for row in connection.execute(text(f"PRAGMA table_info({table})"))
            }
            for name, ddl in columns:
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
