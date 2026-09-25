"""Validation d'URL produit — http/https uniquement, jamais exécutée côté serveur."""

from __future__ import annotations

from urllib.parse import urlparse

ALLOWED_IMAGE_SCHEMES = frozenset({"http", "https"})
BLOCKED_SCHEMES = frozenset({"data", "javascript", "file", "vbscript", "about", "blob"})


def sanitize_http_url(raw: str | None, *, max_length: int = 500) -> str | None:
    """Retourne une URL http(s) normalisée, ou None si absente/invalide."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if len(text) > max_length:
        return None
    # Rejet explicite des schémas dangereux même avant parse.
    lower = text.lower()
    for scheme in BLOCKED_SCHEMES:
        if lower.startswith(f"{scheme}:"):
            return None
    parsed = urlparse(text)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_IMAGE_SCHEMES:
        return None
    if not parsed.netloc:
        return None
    return text
