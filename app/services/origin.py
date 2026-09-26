from __future__ import annotations

from typing import Any

from app.config import Settings
from app.schemas.location import OriginRequest, ResolvedOrigin
from app.services.geocoding import GeocodingError, GeocodingService


class OriginService:
    """Résolution réutilisable par un futur mode Express ; aucun accès au navigateur."""

    def __init__(self, settings: Settings, geocoder: GeocodingService):
        self.settings = settings
        self.geocoder = geocoder

    def resolve(self, origin: OriginRequest | None) -> ResolvedOrigin:
        if origin is None:  # Compatibilité des clients 0.1 sans origine.
            return ResolvedOrigin(
                type="site",
                label="Chantier",
                source="configuration",
                latitude=self.settings.user_latitude,
                longitude=self.settings.user_longitude,
            )
        labels = {
            "site": "Chantier",
            "current_location": "Ma position",
            "company": "Entreprise",
            "other": "Autre adresse",
        }
        if origin.type == "company":
            return self._from_coordinates(
                origin_type="company",
                label=labels[origin.type],
                latitude=self.settings.company_latitude,
                longitude=self.settings.company_longitude,
                address=self.settings.company_address,
                fallback_source="configuration",
            )
        if origin.latitude is not None:
            return self._from_coordinates(
                origin_type=origin.type,
                label=labels[origin.type],
                latitude=origin.latitude,
                longitude=origin.longitude,
                address=None,
                fallback_source="coordinates",
            )
        location = self.geocoder.geocode(origin.address)
        return self._from_geocoding_result(
            location,
            origin_type=origin.type,
            label=labels[origin.type],
            address=origin.address,
        )

    def _from_coordinates(
        self,
        *,
        origin_type: str,
        label: str,
        latitude: float,
        longitude: float,
        address: str | None,
        fallback_source: str,
    ) -> ResolvedOrigin:
        """Enrichit via reverse si possible ; soft-fail → coords seules (routing OK)."""
        try:
            location = self.geocoder.reverse(latitude, longitude)
        except GeocodingError:
            return ResolvedOrigin(
                type=origin_type,  # type: ignore[arg-type]
                label=label,
                source=fallback_source,
                address=address,
                latitude=latitude,
                longitude=longitude,
            )
        return self._from_geocoding_result(
            location,
            origin_type=origin_type,
            label=label,
            address=address or getattr(location, "address", None) or getattr(location, "label", None),
            latitude=latitude,
            longitude=longitude,
            fallback_source=fallback_source,
        )

    def _from_geocoding_result(
        self,
        location: Any,
        *,
        origin_type: str,
        label: str,
        address: str | None,
        latitude: float | None = None,
        longitude: float | None = None,
        fallback_source: str | None = None,
    ) -> ResolvedOrigin:
        source = getattr(location, "source", None) or fallback_source or self.geocoder.provider
        return ResolvedOrigin(
            type=origin_type,  # type: ignore[arg-type]
            label=label,
            source=source,
            address=address,
            latitude=latitude if latitude is not None else location.latitude,
            longitude=longitude if longitude is not None else location.longitude,
            city=getattr(location, "city", None),
            postcode=getattr(location, "postcode", None),
            citycode=getattr(location, "citycode", None),
            score=getattr(location, "score", None),
        )
