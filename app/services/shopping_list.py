"""Liste d'achat : vue snapshot + suivi réel persistant."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.chantier import AchatSuiviLigne, utc_now
from app.repositories.chantiers import ChantierRepository
from app.schemas.shopping_list import (
    ShoppingLineTracking,
    ShoppingLineTrackingRead,
    ShoppingLineTrackingWrite,
    ShoppingListActualTotals,
    ShoppingListChantier,
    ShoppingListLine,
    ShoppingListRead,
    ShoppingListStore,
    ShoppingListTotals,
)
from app.services.approvisionnement import ApprovisionnementService
from app.services.chantiers import ChantierConflict, ChantierNotFound

CENT = Decimal("0.01")


def _dec(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _pack_size(purchased: Decimal, packs: int) -> Decimal | None:
    if packs <= 0:
        return None
    return (purchased / Decimal(packs)).quantize(Decimal("0.001"))


def make_line_key(agency_id: int, product_id: int) -> str:
    """Clé stable d'une ligne snapshot : une ligne = un produit dans une agence."""
    return f"{int(agency_id)}:{int(product_id)}"


def parse_line_key(line_key: str) -> tuple[int, int]:
    parts = (line_key or "").split(":")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise ValueError("Clé de ligne invalide.")
    agency_id, product_id = int(parts[0]), int(parts[1])
    if agency_id < 1 or product_id < 1:
        raise ValueError("Clé de ligne invalide.")
    return agency_id, product_id


def make_snapshot_token(appro) -> str:
    """Identifie une solution retenue (réutilise le même id appro à chaque upsert)."""
    chosen = appro.chosen_at
    if chosen.tzinfo is None:
        chosen = chosen.replace(tzinfo=timezone.utc)
    else:
        chosen = chosen.astimezone(timezone.utc)
    return f"{appro.id}:{appro.strategy_key}:{chosen.isoformat()}"


def _format_address(stop: dict) -> str | None:
    parts = [
        (stop.get("address") or "").strip(),
        " ".join(
            p
            for p in [
                (stop.get("postal_code") or "").strip(),
                (stop.get("city") or "").strip(),
            ]
            if p
        ).strip(),
    ]
    text = ", ".join(p for p in parts if p)
    return text or None


def _leg_minutes_by_agency(route: dict | None) -> dict[int, float]:
    if not route or not isinstance(route.get("legs"), list):
        return {}
    result: dict[int, float] = {}
    for leg in route["legs"]:
        end = leg.get("end") or {}
        agency_id = end.get("agency_id")
        if agency_id is None:
            continue
        minutes = leg.get("duration_minutes")
        if minutes is None:
            continue
        result[int(agency_id)] = float(minutes)
    return result


def _sous_total_reel(quantite: Decimal | None, prix: Decimal | None) -> Decimal | None:
    if quantite is None or prix is None:
        return None
    return (quantite * prix).quantize(CENT, ROUND_HALF_UP)


def _tracking_view(row: AchatSuiviLigne | None, line_total_prevu: Decimal) -> ShoppingLineTracking:
    if row is None:
        return ShoppingLineTracking()
    sous_total = _sous_total_reel(row.quantite_reelle, row.prix_reel)
    renseigne = sous_total is not None
    ecart = None
    if renseigne:
        ecart = (sous_total - line_total_prevu).quantize(CENT, ROUND_HALF_UP)
    return ShoppingLineTracking(
        pris=bool(row.pris),
        quantite_reelle=row.quantite_reelle,
        prix_reel=row.prix_reel,
        sous_total_reel=sous_total,
        ecart=ecart,
        renseigne=renseigne,
        updated_at=row.updated_at,
    )


def build_shopping_list_from_snapshot(
    chantier,
    appro,
    suivi_by_key: dict[str, AchatSuiviLigne] | None = None,
) -> ShoppingListRead:
    header = ShoppingListChantier(
        id=chantier.id,
        nom=chantier.nom,
        client=chantier.client,
        adresse=chantier.adresse,
        date_prevue=str(chantier.date_prevue) if chantier.date_prevue else None,
    )
    if appro is None:
        return ShoppingListRead(available=False, chantier=header)

    suivi_by_key = suivi_by_key or {}
    snapshot = appro.snapshot or {}
    strategy = snapshot.get("strategy") or {}
    stops = list(strategy.get("stops") or [])
    lines = list(strategy.get("lines") or [])
    breakdown = strategy.get("cost_breakdown") or {}
    route = strategy.get("route")
    leg_minutes = _leg_minutes_by_agency(route)
    token = make_snapshot_token(appro)

    stops_by_id = {int(s["id"]): s for s in stops if s.get("id") is not None}
    ordered_ids: list[int] = [int(s["id"]) for s in stops if s.get("id") is not None]
    for line in lines:
        agency_id = int(line["agency_id"])
        if agency_id not in stops_by_id and agency_id not in ordered_ids:
            ordered_ids.append(agency_id)

    lines_by_agency: dict[int, list] = {aid: [] for aid in ordered_ids}
    for line in lines:
        agency_id = int(line["agency_id"])
        lines_by_agency.setdefault(agency_id, []).append(line)

    stores: list[ShoppingListStore] = []
    all_lines: list[ShoppingListLine] = []
    for order, agency_id in enumerate(ordered_ids, start=1):
        agency_lines = lines_by_agency.get(agency_id) or []
        if not agency_lines:
            continue
        stop = stops_by_id.get(agency_id) or {}
        first = agency_lines[0]
        store_lines: list[ShoppingListLine] = []
        subtotal = Decimal("0.00")
        for raw in agency_lines:
            purchased = _dec(raw["purchased_quantity"]) or Decimal("0")
            packs = int(raw.get("packs") or 0)
            line_total = _dec(raw["line_total"]) or Decimal("0.00")
            subtotal += line_total
            product_id = int(raw["product_id"])
            key = make_line_key(agency_id, product_id)
            supplier_unit = raw.get("supplier_unit") or "pack"
            item = ShoppingListLine(
                line_key=key,
                agency_id=agency_id,
                product_id=product_id,
                product_name=raw.get("product_name") or f"Produit #{product_id}",
                supplier_reference=raw.get("supplier_reference") or "",
                reference_unit=raw.get("reference_unit") or "",
                supplier_unit=supplier_unit,
                requested_quantity=_dec(raw["requested_quantity"]) or Decimal("0"),
                purchased_quantity=purchased,
                packs=packs,
                pack_size=_pack_size(purchased, packs),
                pack_price=_dec(raw["pack_price"]) or Decimal("0.00"),
                line_total=line_total,
                preparation_minutes=raw.get("preparation_minutes"),
                available_quantity=_dec(raw.get("available_quantity")),
                price_unit_label=supplier_unit,
                suivi=_tracking_view(suivi_by_key.get(key), line_total),
            )
            store_lines.append(item)
            all_lines.append(item)
        stores.append(
            ShoppingListStore(
                agency_id=agency_id,
                supplier=stop.get("supplier") or first.get("supplier") or "",
                name=stop.get("name") or first.get("supplier") or f"Agence {agency_id}",
                address=_format_address(stop) if stop else None,
                postal_code=stop.get("postal_code"),
                city=stop.get("city"),
                distance_km=stop.get("distance_km"),
                stop_order=order,
                travel_minutes_from_previous=leg_minutes.get(agency_id),
                lines=store_lines,
                subtotal=subtotal,
            )
        )

    material_total = _dec(appro.material_total)
    if material_total is None:
        material_total = _dec(strategy.get("material_total"))

    totals = ShoppingListTotals(
        material_total=material_total,
        distance_cost=_dec(breakdown.get("distance_cost")),
        time_cost=_dec(breakdown.get("time_cost")),
        extra_stops_cost=_dec(breakdown.get("extra_stops_cost")),
        estimated_procurement_cost=_dec(appro.estimated_procurement_cost)
        or _dec(strategy.get("estimated_procurement_cost"))
        or _dec(breakdown.get("estimated_procurement_cost")),
        total_distance_km=_dec(appro.total_distance_km)
        or _dec(strategy.get("total_distance_km")),
        travel_minutes=_dec(appro.travel_minutes) or _dec(strategy.get("travel_minutes")),
    )

    renseignees = [line for line in all_lines if line.suivi.renseigne]
    material_real = None
    ecart_renseignes = None
    if renseignees:
        material_real = sum(
            (line.suivi.sous_total_reel for line in renseignees),
            Decimal("0.00"),
        ).quantize(CENT, ROUND_HALF_UP)
        prevu_renseigne = sum((line.line_total for line in renseignees), Decimal("0.00"))
        ecart_renseignes = (material_real - prevu_renseigne).quantize(CENT, ROUND_HALF_UP)

    taken_count = sum(1 for line in all_lines if line.suivi.pris)
    actual = ShoppingListActualTotals(
        lines_renseignees=len(renseignees),
        lines_total=len(all_lines),
        material_total_renseigne=material_real,
        ecart_materiaux_renseignes=ecart_renseignes,
        taken_count=taken_count,
    )

    return ShoppingListRead(
        available=True,
        obsolete=bool(appro.obsolete),
        chantier=header,
        approvisionnement_id=appro.id,
        snapshot_token=token,
        strategy_key=appro.strategy_key or strategy.get("key"),
        strategy_title=appro.strategy_title or strategy.get("title"),
        chosen_at=appro.chosen_at,
        currency=appro.currency or snapshot.get("currency") or "EUR",
        tax_basis=appro.tax_basis or snapshot.get("tax_basis") or "HT",
        stores=stores,
        totals=totals,
        actual=actual,
        line_count=len(all_lines),
        taken_count=taken_count,
    )


class ShoppingListService:
    def __init__(self, session: Session):
        self.session = session
        self.chantiers = ChantierRepository(session)
        self.approvisionnement = ApprovisionnementService(session)

    def _load_suivi(self, chantier_id: int, token: str) -> dict[str, AchatSuiviLigne]:
        rows = self.session.scalars(
            select(AchatSuiviLigne).where(
                AchatSuiviLigne.chantier_id == chantier_id,
                AchatSuiviLigne.snapshot_token == token,
            )
        ).all()
        return {row.line_key: row for row in rows}

    def _snapshot_line(self, appro, line_key: str) -> dict:
        try:
            agency_id, product_id = parse_line_key(line_key)
        except ValueError as error:
            raise ChantierNotFound("Ligne introuvable dans cette liste d’achat.") from error
        strategy = (appro.snapshot or {}).get("strategy") or {}
        for raw in strategy.get("lines") or []:
            if int(raw["agency_id"]) == agency_id and int(raw["product_id"]) == product_id:
                return raw
        raise ChantierNotFound("Ligne introuvable dans cette liste d’achat.")

    def get(self, chantier_id: int) -> ShoppingListRead:
        chantier = self.chantiers.get(chantier_id)
        if chantier is None:
            raise ChantierNotFound("Chantier introuvable.")
        try:
            appro = self.approvisionnement.get(chantier_id)
        except ChantierNotFound as error:
            if "approvisionnement" not in str(error).lower():
                raise
            return build_shopping_list_from_snapshot(chantier, None)
        token = make_snapshot_token(appro)
        suivi = self._load_suivi(chantier_id, token)
        return build_shopping_list_from_snapshot(chantier, appro, suivi)

    def update_line(
        self, chantier_id: int, line_key: str, payload: ShoppingLineTrackingWrite
    ) -> ShoppingLineTrackingRead:
        chantier = self.chantiers.get(chantier_id)
        if chantier is None:
            raise ChantierNotFound("Chantier introuvable.")
        try:
            appro = self.approvisionnement.get(chantier_id)
        except ChantierNotFound as error:
            if "approvisionnement" not in str(error).lower():
                raise
            raise ChantierNotFound("Aucun approvisionnement retenu.") from error

        token = make_snapshot_token(appro)
        raw = self._snapshot_line(appro, line_key)
        line_total_prevu = _dec(raw["line_total"]) or Decimal("0.00")
        packs_prevus = int(raw.get("packs") or 0)
        pack_price_prevu = _dec(raw.get("pack_price")) or Decimal("0.00")

        row = self.session.scalar(
            select(AchatSuiviLigne).where(
                AchatSuiviLigne.chantier_id == chantier_id,
                AchatSuiviLigne.snapshot_token == token,
                AchatSuiviLigne.line_key == line_key,
            )
        )

        if row is None:
            if payload.updated_at is not None:
                raise ChantierConflict(
                    "Cette ligne a été modifiée ailleurs. Rechargez la liste d’achat."
                )
            row = AchatSuiviLigne(
                chantier_id=chantier_id,
                approvisionnement_id=appro.id,
                snapshot_token=token,
                line_key=line_key,
                pris=payload.pris,
                quantite_reelle=payload.quantite_reelle,
                prix_reel=payload.prix_reel,
            )
            self.session.add(row)
        else:
            expected = payload.updated_at
            if expected is None:
                raise ChantierConflict(
                    "Cette ligne a été modifiée ailleurs. Rechargez la liste d’achat."
                )
            token_ts = expected.astimezone(timezone.utc)
            current = row.updated_at
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            else:
                current = current.astimezone(timezone.utc)
            if current != token_ts:
                raise ChantierConflict(
                    "Cette ligne a été modifiée ailleurs. Rechargez la liste d’achat."
                )
            row.pris = payload.pris
            row.quantite_reelle = payload.quantite_reelle
            row.prix_reel = payload.prix_reel
            row.updated_at = max(utc_now(), current + timedelta(microseconds=1))

        self.session.flush()
        self.session.commit()
        self.session.refresh(row)

        sous_total = _sous_total_reel(row.quantite_reelle, row.prix_reel)
        renseigne = sous_total is not None
        ecart = None
        if renseigne:
            ecart = (sous_total - line_total_prevu).quantize(CENT, ROUND_HALF_UP)

        return ShoppingLineTrackingRead(
            line_key=line_key,
            snapshot_token=token,
            pris=bool(row.pris),
            quantite_reelle=row.quantite_reelle,
            prix_reel=row.prix_reel,
            sous_total_reel=sous_total,
            ecart=ecart,
            renseigne=renseigne,
            updated_at=row.updated_at,
            pack_price_prevu=pack_price_prevu,
            packs_prevus=packs_prevus,
            line_total_prevu=line_total_prevu,
        )
