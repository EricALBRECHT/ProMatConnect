from app.config import Settings
from app.schemas.location import OriginRequest, ResolvedOrigin
from app.services.geocoding import GeocodingService


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
            return ResolvedOrigin(
                type="company",
                label=labels[origin.type],
                source="configuration",
                address=self.settings.company_address,
                latitude=self.settings.company_latitude,
                longitude=self.settings.company_longitude,
            )
        if origin.latitude is not None:
            return ResolvedOrigin(
                type=origin.type,
                label=labels[origin.type],
                source="coordinates",
                latitude=origin.latitude,
                longitude=origin.longitude,
            )
        coordinates = self.geocoder.geocode(origin.address)
        return ResolvedOrigin(
            **coordinates.model_dump(),
            type=origin.type,
            label=labels[origin.type],
            address=origin.address,
            source=self.geocoder.provider,
        )
