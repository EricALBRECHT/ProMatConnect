"""Géocodage injectable, sans réseau dans le MVP 0.2."""

import unicodedata
from abc import ABC, abstractmethod

from app.schemas.location import Coordinates


class AddressNotFound(ValueError):
    pass


class GeocodingService(ABC):
    provider = "geocoding"

    @abstractmethod
    def geocode(self, address: str) -> Coordinates: ...


class FakeGeocodingService(GeocodingService):
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

    def geocode(self, address: str) -> Coordinates:
        entries = {self.normalize(key): value for key, value in self.ADDRESSES.items()}
        result = entries.get(self.normalize(address))
        if result is None:
            raise AddressNotFound(
                "Adresse inconnue du géocodeur de démonstration. "
                "Choisissez une adresse de test proposée ou utilisez Ma position."
            )
        return result
