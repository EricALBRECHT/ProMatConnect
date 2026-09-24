"""Politique de cleanup miroir (Python) pour tests sans Node dans l'image Docker."""


class ChantierCleanupTracker:
    def __init__(self):
        self._created: set[int] = set()

    def track(self, chantier_id: int) -> int:
        numeric = int(chantier_id)
        if numeric < 1:
            raise ValueError(f"ID invalide : {chantier_id}")
        self._created.add(numeric)
        return numeric

    def cleanup_ids(self) -> list[int]:
        return sorted(self._created)

    def ids_matching_name(self, mapping: dict[str, str], name: str) -> list[int]:
        # Intentionnellement vide : le nom n'est jamais un critère de suppression.
        return []
