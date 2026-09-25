"""Liste d'achat : vue métier dérivée du snapshot d'approvisionnement retenu."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.repositories.chantiers import ChantierRepository
from app.schemas.shopping_list import (
    ShoppingListChantier,
    ShoppingListLine,
    ShoppingListRead,
    ShoppingListStore,
    ShoppingListTotals,
)
from app.services.approvisionnement import ApprovisionnementService
from app.services.chantiers import ChantierNotFound


def _dec(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _pack_size(purchased: Decimal, packs: int) -> Decimal | None:
    if packs <= 0:
        return None
    return (purchased / Decimal(packs)).quantize(Decimal("0.001"))


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
    """Durée du leg qui arrive à chaque agence (si route snapshotée)."""
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


def build_shopping_list_from_snapshot(
    chantier,
    appro,
) -> ShoppingListRead:
    """Transforme chantier + ApprovisionnementRead en vue liste d'achat."""
    header = ShoppingListChantier(
        id=chantier.id,
        nom=chantier.nom,
        client=chantier.client,
        adresse=chantier.adresse,
        date_prevue=str(chantier.date_prevue) if chantier.date_prevue else None,
    )
    if appro is None:
        return ShoppingListRead(available=False, chantier=header)

    snapshot = appro.snapshot or {}
    strategy = snapshot.get("strategy") or {}
    stops = list(strategy.get("stops") or [])
    lines = list(strategy.get("lines") or [])
    breakdown = strategy.get("cost_breakdown") or {}
    route = strategy.get("route")
    leg_minutes = _leg_minutes_by_agency(route)

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
            store_lines.append(
                ShoppingListLine(
                    product_id=int(raw["product_id"]),
                    product_name=raw.get("product_name") or f"Produit #{raw['product_id']}",
                    supplier_reference=raw.get("supplier_reference") or "",
                    reference_unit=raw.get("reference_unit") or "",
                    supplier_unit=raw.get("supplier_unit") or "",
                    requested_quantity=_dec(raw["requested_quantity"]) or Decimal("0"),
                    purchased_quantity=purchased,
                    packs=packs,
                    pack_size=_pack_size(purchased, packs),
                    pack_price=_dec(raw["pack_price"]) or Decimal("0.00"),
                    line_total=line_total,
                    preparation_minutes=raw.get("preparation_minutes"),
                    available_quantity=_dec(raw.get("available_quantity")),
                )
            )
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

    return ShoppingListRead(
        available=True,
        obsolete=bool(appro.obsolete),
        chantier=header,
        strategy_key=appro.strategy_key or strategy.get("key"),
        strategy_title=appro.strategy_title or strategy.get("title"),
        chosen_at=appro.chosen_at,
        currency=appro.currency or snapshot.get("currency") or "EUR",
        tax_basis=appro.tax_basis or snapshot.get("tax_basis") or "HT",
        stores=stores,
        totals=totals,
        line_count=sum(len(store.lines) for store in stores),
    )


class ShoppingListService:
    def __init__(self, session: Session):
        self.session = session
        self.chantiers = ChantierRepository(session)
        self.approvisionnement = ApprovisionnementService(session)

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
        return build_shopping_list_from_snapshot(chantier, appro)
