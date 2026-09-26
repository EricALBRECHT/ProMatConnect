"""Géocodage injectable — Fake (tests/démo) ou Géoplateforme IGN."""

from __future__ import annotations

import json
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, Callable

from app.schemas.location import Coordinates, GeocodingResult

GEOPF_SEARCH_URL = "https://data.geopf.fr/geocodage/search"
GEOPF_REVERSE_URL = "https://data.geopf.fr/geocodage/reverse"
DEFAULT_GEOPF_TIMEOUT_S = 10.0
GEOPF_PROVIDER = "geopf"


class GeocodingError(ValueError):
    """Erreur de géocodage destinée à un message utilisateur (pas d'exception brute)."""


class AddressNotFound(GeocodingError):
    pass


class GeocodingUnavailable(GeocodingError):
    """Service distant indisponible (HTTP, timeout, JSON…)."""


class GeocodingService(ABC):
    provider = "geocoding"

    @abstractmethod
    def geocode(self, address: str) -> GeocodingResult:
        """Adresse texte → coordonnées (+ commune / CP / INSEE si disponibles)."""

    def reverse(self, latitude: float, longitude: float) -> GeocodingResult:
        """Coordonnées → adresse / commune / CP / INSEE.

        Défaut : non supporté (Fake conserve le comportement historique).
        """
        raise AddressNotFound("Géocodage inverse non disponible pour ce fournisseur.")


class FakeGeocodingService(GeocodingService):
    """Géocodeur déterministe hors-réseau — réservé aux tests et à la démo locale."""

    provider = "fake_geocoding"

    ADDRESSES = {
        "10 rue du Chantier, 75004 Paris": Coordinates(latitude=48.8566, longitude=2.3522),
        "20 rue de l'Entreprise, 75011 Paris": Coordinates(latitude=48.8600, longitude=2.3800),
        "5 rue des Artisans, 94200 Ivry-sur-Seine": Coordinates(latitude=48.8120, longitude=2.3850),
        "8 rue du Depot, 93200 Saint-Denis": Coordinates(latitude=48.9250, longitude=2.3550),
    }

    @staticmethod
    def normalize(address: str) -> str:
        text = unicodedata.normalize("NFKD", address.casefold())
        return " ".join(
            "".join(c if c.isalnum() else " " for c in text if not unicodedata.combining(c)).split()
        )

    def geocode(self, address: str) -> GeocodingResult:
        if not str(address or "").strip():
            raise AddressNotFound("Adresse vide.")
        needle = self.normalize(address)
        for key, value in self.ADDRESSES.items():
            if self.normalize(key) == needle:
                # Libellé canonique (clé) pour conserver l'égalité déterministe des tests.
                return GeocodingResult(
                    latitude=value.latitude,
                    longitude=value.longitude,
                    label=key,
                    address=key,
                    source=self.provider,
                )
        raise AddressNotFound(
            "Adresse inconnue du géocodeur de démonstration. "
            "Choisissez une adresse de test proposée ou utilisez Ma position."
        )


# ---------------------------------------------------------------------------
# Client HTTP Géoplateforme (transport injectable)
# ---------------------------------------------------------------------------

GetTransport = Callable[..., tuple[int, bytes]]


def default_get_transport(*, url: str, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "ProMatConnect-GeopfGeocoder/0.1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 200))
            raw = resp.read()
            return status, raw
    except TimeoutError as exc:
        raise GeocodingUnavailable(f"Délai de géocodage dépassé ({timeout}s).") from exc
    except urllib.error.HTTPError as exc:
        body = (exc.read()[:800] if exc.fp else b"").decode("utf-8", errors="replace")
        raise GeocodingUnavailable(_http_message(int(exc.code), body)) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            raise GeocodingUnavailable(f"Délai de géocodage dépassé ({timeout}s).") from exc
        raise GeocodingUnavailable(f"Service de géocodage inaccessible ({reason}).") from exc


def _http_message(status: int, detail: str = "") -> str:
    if status == 429:
        return "Service de géocodage temporairement saturé (HTTP 429). Réessayez plus tard."
    if status >= 500:
        return f"Service de géocodage indisponible (HTTP {status})."
    return f"Erreur de géocodage (HTTP {status})."


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        for item in value:
            text = _as_str(item)
            if text:
                return text
        return None
    text = str(value).strip()
    return text if text else None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _feature_score(properties: dict[str, Any]) -> float | None:
    for key in ("score", "_score"):
        score = _as_float(properties.get(key))
        if score is not None:
            return score
    return None


def parse_geopf_feature(feature: Any, *, source: str = GEOPF_PROVIDER) -> GeocodingResult | None:
    """Parse une feature GeoJSON ; None si coords invalides (citycode peut manquer)."""
    if not isinstance(feature, dict):
        return None
    geometry = feature.get("geometry") or {}
    coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        return None
    lon = _as_float(coords[0])
    lat = _as_float(coords[1])
    if lat is None or lon is None or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None
    props = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
    label = _as_str(props.get("label")) or _as_str(props.get("name")) or _as_str(props.get("toponym"))
    return GeocodingResult(
        latitude=lat,
        longitude=lon,
        label=label,
        address=label,
        city=_as_str(props.get("city")),
        postcode=_as_str(props.get("postcode")),
        citycode=_as_str(props.get("citycode")),
        score=_feature_score(props),
        source=source,
    )


def select_best_feature(
    payload: Any, *, source: str = GEOPF_PROVIDER, require_citycode: bool = True
) -> GeocodingResult:
    """Choisit le meilleur résultat ; exige un citycode API explicite par défaut."""
    if not isinstance(payload, dict):
        raise AddressNotFound("Réponse de géocodage invalide.")
    features = payload.get("features")
    if not isinstance(features, list) or not features:
        raise AddressNotFound("Aucun résultat de géocodage pour cette localisation.")

    parsed: list[GeocodingResult] = []
    missing_citycode = False
    for feature in features:
        result = parse_geopf_feature(feature, source=source)
        if result is None:
            continue
        if require_citycode and not result.citycode:
            missing_citycode = True
            continue
        parsed.append(result)

    if not parsed:
        if missing_citycode:
            raise AddressNotFound(
                "Résultat de géocodage sans code INSEE (citycode). "
                "Impossible de confirmer la commune."
            )
        raise AddressNotFound("Aucun résultat de géocodage exploitable.")

    def sort_key(item: GeocodingResult) -> float:
        return item.score if item.score is not None else float("-inf")

    return max(parsed, key=sort_key)


class GeopfGeocodingService(GeocodingService):
    """Géocodage français via Géoplateforme IGN (search + reverse)."""

    provider = GEOPF_PROVIDER

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_GEOPF_TIMEOUT_S,
        transport: GetTransport | None = None,
        search_url: str = GEOPF_SEARCH_URL,
        reverse_url: str = GEOPF_REVERSE_URL,
    ):
        self.timeout = float(timeout)
        self.transport = transport or default_get_transport
        self.search_url = search_url
        self.reverse_url = reverse_url

    def geocode(self, address: str) -> GeocodingResult:
        query = str(address or "").strip()
        if not query:
            raise AddressNotFound("Adresse vide.")
        params = {"q": query, "index": "address", "limit": "5"}
        payload = self._get_json(self.search_url, params)
        return select_best_feature(payload, source=self.provider)

    def reverse(self, latitude: float, longitude: float) -> GeocodingResult:
        try:
            lat = float(latitude)
            lon = float(longitude)
        except (TypeError, ValueError) as exc:
            raise AddressNotFound("Coordonnées invalides.") from exc
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            raise AddressNotFound("Coordonnées hors bornes.")
        params = {"lat": str(lat), "lon": str(lon), "index": "address", "limit": "5"}
        payload = self._get_json(self.reverse_url, params)
        return select_best_feature(payload, source=self.provider)

    def _get_json(self, base_url: str, params: dict[str, str]) -> Any:
        url = f"{base_url}?{urllib.parse.urlencode(params)}"
        try:
            status, raw = self.transport(url=url, timeout=self.timeout)
        except GeocodingError:
            raise
        except TimeoutError as exc:
            raise GeocodingUnavailable(f"Délai de géocodage dépassé ({self.timeout}s).") from exc
        except Exception as exc:  # noqa: BLE001 — frontière transport mockable
            raise GeocodingUnavailable(f"Service de géocodage inaccessible ({exc}).") from exc

        if status == 429 or status >= 500 or status != 200:
            raise GeocodingUnavailable(_http_message(status))

        try:
            text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
            return json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GeocodingUnavailable("Réponse de géocodage illisible (JSON invalide).") from exc


def build_geocoding_service(provider: str, *, timeout: float = DEFAULT_GEOPF_TIMEOUT_S) -> GeocodingService:
    """Factory configuration : fake (défaut tests/démo) ou geopf."""
    key = (provider or "fake").strip().lower()
    if key in ("geopf", "geoplateforme", "ign"):
        return GeopfGeocodingService(timeout=timeout)
    return FakeGeocodingService()
