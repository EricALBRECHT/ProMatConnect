"""Connecteur d'import fichier CSV → modèles normalisés (sans écriture DB)."""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import BinaryIO

from app.connectors.base import ConnectorHealth, ConnectorOffer, SupplierConnector
from app.connectors.normalized import NormalizedAgency, NormalizedOffer, NormalizedSupplierProduct
from app.connectors.url_safety import sanitize_http_url
from app.services.tax import parse_vat_rate, validate_vat_rate

# price n'est plus obligatoire : une ligne sans prix reste une référence catalogue.
# agency_external_id doit être présent en en-tête mais peut être vide
# (catalogue national / public sans magasin).
REQUIRED_COLUMNS = (
    "supplier",
    "agency_external_id",
    "product_external_reference",
    "product_name",
    "currency",
    "tax_basis",
)

# Alias d'en-têtes acceptés → nom canonique v0.7
HEADER_ALIASES = {
    "supplier_reference": "product_external_reference",
    "product_reference": "product_external_reference",
    "external_reference": "product_external_reference",
}

OPTIONAL_COLUMNS = (
    "agency_name",
    "agency_address",
    "agency_postal_code",
    "agency_city",
    "agency_latitude",
    "agency_longitude",
    "brand",
    "ean",
    "supplier_unit",
    "packaging_quantity",
    "reference_unit",
    "reference_quantity",
    "available_quantity",
    "preparation_minutes",
    "observed_at",
    "product_code",
    "price",
    "source_url",
    "seller",
    "verification_status",
    "image_url",
    "vat_rate",
)

ALLOWED_CURRENCIES = frozenset({"EUR"})
ALLOWED_TAX_BASIS = frozenset({"HT", "TTC"})
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
_SAFE_FILENAME = re.compile(r"^[\w.\- ()]+\.csv$", re.UNICODE)


class ImportIssue(dict):
    """Erreur ou avertissement de validation (ligne 1-indexée, 0 = en-tête)."""

    @staticmethod
    def make(line: int, code: str, message: str) -> dict:
        return {"line": line, "code": code, "message": message}


class FileSupplierConnector(SupplierConnector):
    """Parse un CSV fournisseur vers NormalizedOffer — pas d'accès live distant."""

    def __init__(self, offers: list[NormalizedOffer] | None = None, supplier_key: str = "file"):
        self._offers = list(offers or [])
        self._supplier_key = supplier_key
        self._detected_columns: list[str] = []
        self._delimiter: str = ","

    @property
    def connector_key(self) -> str:
        return f"file:{self._supplier_key}"

    @property
    def supplier_key(self) -> str:
        return self._supplier_key

    @property
    def supplier_name(self) -> str:
        return self._supplier_key

    @property
    def display_name(self) -> str:
        return f"Import fichier ({self._supplier_key})"

    @property
    def source_type(self) -> str:
        return "file"

    def get_offers(self, product_ids: list[int]) -> list[ConnectorOffer]:
        return []

    def health(self) -> ConnectorHealth:
        return ConnectorHealth(
            ok=True,
            connector_key=self.connector_key,
            supplier_key=self.supplier_key,
            source_type=self.source_type,
            detail=f"{len(self._offers)} lignes normalisées en mémoire",
        )

    @property
    def normalized_offers(self) -> list[NormalizedOffer]:
        return list(self._offers)

    @classmethod
    def validate_filename(cls, filename: str | None) -> str:
        name = (filename or "").split("/")[-1].split("\\")[-1].strip()
        if not name or not name.lower().endswith(".csv"):
            raise ValueError("Seuls les fichiers .csv sont acceptés.")
        if len(name) > 180:
            raise ValueError("Nom de fichier trop long.")
        if not _SAFE_FILENAME.match(name):
            raise ValueError("Nom de fichier invalide (caractères non autorisés).")
        return name

    @classmethod
    def parse_upload(
        cls, data: bytes | BinaryIO, *, filename: str | None = None
    ) -> tuple[FileSupplierConnector, list[dict], list[dict]]:
        """Analyse stricte. Retourne (connecteur, errors, warnings). Aucune écriture DB."""
        if hasattr(data, "read"):
            raw = data.read()
        else:
            raw = data
        if not isinstance(raw, (bytes, bytearray)):
            raise ValueError("Contenu fichier invalide.")
        if len(raw) == 0:
            return cls(), [ImportIssue.make(0, "empty_file", "Fichier vide.")], []
        if len(raw) > MAX_UPLOAD_BYTES:
            return (
                cls(),
                [
                    ImportIssue.make(
                        0,
                        "file_too_large",
                        f"Fichier trop volumineux (max {MAX_UPLOAD_BYTES // 1024} Ko).",
                    )
                ],
                [],
            )
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            return (
                cls(),
                [ImportIssue.make(0, "encoding", "Le fichier doit être encodé en UTF-8.")],
                [],
            )

        sample = text[:4096]
        delimiter = ","
        if sample.count(";") > sample.count(",") and "\n" in sample:
            delimiter = ";"

        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        if reader.fieldnames is None:
            return cls(), [ImportIssue.make(0, "header", "En-tête CSV manquant.")], []

        raw_headers = [h.strip() for h in reader.fieldnames if h is not None and h.strip()]
        headers = [HEADER_ALIASES.get(h, h) for h in raw_headers]
        # Ré-applique les alias sur chaque ligne via fieldnames renommés.
        if reader.fieldnames is not None:
            reader.fieldnames = [
                HEADER_ALIASES.get((h or "").strip(), (h or "").strip()) if h is not None else h
                for h in reader.fieldnames
            ]

        missing = [c for c in REQUIRED_COLUMNS if c not in headers]
        errors: list[dict] = []
        warnings: list[dict] = []
        if missing:
            errors.append(
                ImportIssue.make(
                    0,
                    "missing_columns",
                    f"Colonnes obligatoires absentes : {', '.join(missing)}. "
                    f"Colonnes détectées : {', '.join(raw_headers) or '(aucune)'}.",
                )
            )
            empty = cls()
            empty._detected_columns = list(raw_headers)
            empty._delimiter = delimiter
            return empty, errors, warnings

        offers: list[NormalizedOffer] = []
        seen_keys: set[tuple[str, str, str]] = set()
        suppliers: set[str] = set()

        for line_no, row in enumerate(reader, start=2):
            if row is None:
                continue
            cleaned = {
                (k or "").strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()
            }
            if all(not (v or "").strip() for v in cleaned.values() if v is not None):
                continue

            row_errors, row_warnings, offer = cls._parse_row(line_no, cleaned)
            errors.extend(row_errors)
            warnings.extend(row_warnings)
            if offer is None:
                continue
            key = (
                offer.supplier,
                offer.agency.external_id or "",
                offer.product.external_reference,
            )
            if key in seen_keys:
                errors.append(
                    ImportIssue.make(
                        line_no,
                        "duplicate",
                        "Doublon : même fournisseur + agence + référence produit.",
                    )
                )
                continue
            seen_keys.add(key)
            suppliers.add(offer.supplier)
            offers.append(offer)

        supplier_key = next(iter(suppliers)) if len(suppliers) == 1 else "multi"
        if len(suppliers) > 1:
            warnings.append(
                ImportIssue.make(
                    0,
                    "multi_supplier",
                    f"Plusieurs fournisseurs dans le fichier : {', '.join(sorted(suppliers))}.",
                )
            )
        connector = cls(offers, supplier_key=supplier_key)
        connector._detected_columns = list(raw_headers)
        connector._delimiter = delimiter
        return connector, errors, warnings

    @classmethod
    def _parse_row(
        cls, line_no: int, row: dict
    ) -> tuple[list[dict], list[dict], NormalizedOffer | None]:
        errors: list[dict] = []
        warnings: list[dict] = []

        def req(field: str) -> str:
            value = (row.get(field) or "").strip()
            if not value:
                errors.append(
                    ImportIssue.make(line_no, "required", f"Champ obligatoire vide : {field}.")
                )
            return value

        supplier = req("supplier")
        # Catalogue national : agency_external_id peut être vide (pas d'agence magasin).
        agency_ext = (row.get("agency_external_id") or "").strip()
        product_ref = req("product_external_reference")
        product_name = req("product_name")
        currency = req("currency").upper()
        tax_basis = req("tax_basis").upper()
        price_raw = (row.get("price") or "").strip()

        price: Decimal | None = None
        if price_raw:
            try:
                price = Decimal(price_raw.replace(",", "."))
            except InvalidOperation:
                errors.append(ImportIssue.make(line_no, "invalid_price", "Prix invalide."))
            else:
                if price < 0:
                    errors.append(ImportIssue.make(line_no, "negative_price", "Prix négatif."))
        else:
            warnings.append(
                ImportIssue.make(
                    line_no,
                    "missing_price",
                    "Prix absent — référence catalogue sans offre active.",
                )
            )

        if currency and currency not in ALLOWED_CURRENCIES:
            errors.append(
                ImportIssue.make(
                    line_no,
                    "invalid_currency",
                    f"Devise non supportée : {currency} (attendu EUR).",
                )
            )
        if tax_basis and tax_basis not in ALLOWED_TAX_BASIS:
            errors.append(
                ImportIssue.make(
                    line_no,
                    "invalid_tax_basis",
                    f"Base fiscale invalide : {tax_basis} (attendu HT ou TTC).",
                )
            )

        vat_rate: Decimal | None = None
        vat_raw = (row.get("vat_rate") or "").strip()
        if vat_raw:
            try:
                vat_rate = parse_vat_rate(vat_raw)
            except Exception:
                errors.append(
                    ImportIssue.make(line_no, "invalid_vat_rate", "vat_rate invalide.")
                )
                vat_rate = None
            else:
                if vat_rate is not None and not validate_vat_rate(vat_rate):
                    errors.append(
                        ImportIssue.make(
                            line_no,
                            "invalid_vat_rate",
                            "vat_rate hors plage raisonnable (0–100).",
                        )
                    )
                    vat_rate = None

        packaging: Decimal | None = None
        pack_raw = (row.get("packaging_quantity") or "").strip()
        if pack_raw:
            try:
                packaging = Decimal(pack_raw.replace(",", "."))
                if packaging <= 0:
                    errors.append(
                        ImportIssue.make(
                            line_no, "invalid_quantity", "packaging_quantity doit être > 0."
                        )
                    )
                    packaging = None
            except InvalidOperation:
                errors.append(
                    ImportIssue.make(line_no, "invalid_quantity", "packaging_quantity invalide.")
                )

        ref_qty: Decimal | None = None
        ref_qty_raw = (row.get("reference_quantity") or "").strip()
        if ref_qty_raw:
            try:
                ref_qty = Decimal(ref_qty_raw.replace(",", "."))
                if ref_qty <= 0:
                    errors.append(
                        ImportIssue.make(
                            line_no, "invalid_quantity", "reference_quantity doit être > 0."
                        )
                    )
                    ref_qty = None
            except InvalidOperation:
                errors.append(
                    ImportIssue.make(line_no, "invalid_quantity", "reference_quantity invalide.")
                )

        available: Decimal | None = None
        avail_raw = (row.get("available_quantity") or "").strip()
        if avail_raw:
            try:
                available = Decimal(avail_raw.replace(",", "."))
                if available < 0:
                    errors.append(
                        ImportIssue.make(
                            line_no,
                            "invalid_quantity",
                            "available_quantity ne peut pas être négative.",
                        )
                    )
                    available = None
            except InvalidOperation:
                errors.append(
                    ImportIssue.make(line_no, "invalid_quantity", "available_quantity invalide.")
                )

        prep: int | None = None
        prep_raw = (row.get("preparation_minutes") or "").strip()
        if prep_raw:
            try:
                prep = int(prep_raw)
                if prep < 0:
                    errors.append(
                        ImportIssue.make(
                            line_no, "invalid_quantity", "preparation_minutes négatif."
                        )
                    )
                    prep = None
            except ValueError:
                errors.append(
                    ImportIssue.make(line_no, "invalid_quantity", "preparation_minutes invalide.")
                )

        observed: datetime | None = None
        obs_raw = (row.get("observed_at") or "").strip()
        if obs_raw:
            observed = cls._parse_datetime(obs_raw)
            if observed is None:
                errors.append(ImportIssue.make(line_no, "invalid_date", "observed_at invalide."))

        lat = cls._optional_float(row.get("agency_latitude"))
        lon = cls._optional_float(row.get("agency_longitude"))
        if row.get("agency_latitude") and lat is None:
            errors.append(ImportIssue.make(line_no, "invalid_coord", "agency_latitude invalide."))
        if row.get("agency_longitude") and lon is None:
            errors.append(ImportIssue.make(line_no, "invalid_coord", "agency_longitude invalide."))

        image_raw = (row.get("image_url") or "").strip()
        image_url: str | None = None
        if image_raw:
            image_url = sanitize_http_url(image_raw)
            if image_url is None:
                warnings.append(
                    ImportIssue.make(
                        line_no,
                        "invalid_image_url",
                        "image_url ignorée (seuls http:// et https:// sont acceptés).",
                    )
                )

        source_url_raw = (row.get("source_url") or "").strip()
        source_url: str | None = None
        if source_url_raw:
            source_url = sanitize_http_url(source_url_raw)
            if source_url is None:
                warnings.append(
                    ImportIssue.make(
                        line_no,
                        "invalid_source_url",
                        "source_url ignorée (seuls http:// et https:// sont acceptés).",
                    )
                )

        # Ossature / plaques : quantité = pièces ; dimensions techniques ≠ unité de besoin.
        name_l = product_name.lower()
        ref_unit_raw = (row.get("reference_unit") or "").strip().lower()
        meter_units = {
            "m",
            "ml",
            "mètre",
            "metre",
            "mètres",
            "metres",
        }
        area_units = {"m2", "m²", "m^2", "metre2", "mètre2"}
        if any(token in name_l for token in ("rail", "montant", "fourrure")) and ref_unit_raw in meter_units:
            errors.append(
                ImportIssue.make(
                    line_no,
                    "ossature_unit_mismatch",
                    "Ossature (rail/montant/fourrure) : reference_unit=m incorrect — "
                    "non importable. Corriger en reference_unit=pièce (ou piece) et "
                    "reference_quantity = nombre de pièces par conditionnement "
                    "(ex. lot de 10 → packaging_quantity=10, reference_quantity=10). "
                    "La longueur reste une caractéristique technique (length_mm).",
                )
            )
        if "plaque" in name_l and ref_unit_raw in area_units:
            errors.append(
                ImportIssue.make(
                    line_no,
                    "plaque_unit_mismatch",
                    "Plaque de plâtre : reference_unit=m² incorrect — non importable. "
                    "Corriger en reference_unit=pièce et reference_quantity = nombre "
                    "de plaques par conditionnement (souvent 1). "
                    "La surface reste une caractéristique technique (surface_m2).",
                )
            )

        if errors:
            return errors, warnings, None

        agency_name = (row.get("agency_name") or "").strip() or None
        if agency_ext and not agency_name:
            warnings.append(
                ImportIssue.make(
                    line_no, "missing_agency_name", "agency_name absent — valeur dérivée."
                )
            )
        if agency_ext and (lat is None or lon is None):
            warnings.append(
                ImportIssue.make(
                    line_no,
                    "missing_coords",
                    "Coordonnées d'agence absentes — agence non géolocalisée "
                    "(prix matériaux comparables ; calculs distance/trajet exclus).",
                )
            )

        offer = NormalizedOffer(
            supplier=supplier,
            agency=NormalizedAgency(
                external_id=agency_ext,
                name=agency_name,
                address=(row.get("agency_address") or "").strip() or None,
                postal_code=(row.get("agency_postal_code") or "").strip() or None,
                city=(row.get("agency_city") or "").strip() or None,
                latitude=lat,
                longitude=lon,
            ),
            product=NormalizedSupplierProduct(
                external_reference=product_ref,
                name=product_name,
                brand=(row.get("brand") or "").strip() or None,
                supplier_unit=(row.get("supplier_unit") or "").strip() or None,
                packaging_quantity=packaging,
                reference_unit=(row.get("reference_unit") or "").strip() or None,
                reference_quantity=ref_qty,
                ean=(row.get("ean") or "").strip() or None,
                product_code=(row.get("product_code") or "").strip() or None,
                image_url=image_url,
            ),
            price=price,
            currency=currency,
            tax_basis=tax_basis,
            vat_rate=vat_rate,
            available_quantity=available,
            preparation_minutes=prep,
            observed_at=observed,
            source_url=source_url,
            seller=(row.get("seller") or "").strip() or None,
            verification_status=(row.get("verification_status") or "").strip() or None,
        )
        return errors, warnings, offer

    @staticmethod
    def _optional_float(raw: str | None) -> float | None:
        text = (raw or "").strip()
        if not text:
            return None
        try:
            return float(text.replace(",", "."))
        except ValueError:
            return None

    @staticmethod
    def _parse_datetime(raw: str) -> datetime | None:
        text = raw.strip().replace("Z", "+00:00")
        try:
            value = datetime.fromisoformat(text)
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                value = datetime.strptime(text, fmt)
                return value.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None
