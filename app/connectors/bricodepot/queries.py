"""Chargement des documents GraphQL Brico Dépôt (source canonique).

Canonical : app/connectors/bricodepot/queries/*.graphql
Les probes tools/ peuvent recharger ces fichiers pour éviter deux versions divergentes.
"""

from __future__ import annotations

from pathlib import Path

QUERIES_DIR = Path(__file__).resolve().parent / "queries"


def _strip_hash_comments(text: str) -> str:
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
    return "\n".join(lines).strip()


def load_query(name: str) -> str:
    path = QUERIES_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Requête GraphQL Brico Dépôt introuvable: {path}")
    text = _strip_hash_comments(path.read_text(encoding="utf-8"))
    if not text or "{" not in text:
        raise ValueError(f"Requête GraphQL invalide: {path}")
    return text


def load_product_query() -> str:
    return load_query("product.graphql")


def load_stores_query() -> str:
    return load_query("stores.graphql")
