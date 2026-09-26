#!/usr/bin/env python3
"""Probe manuel Géoplateforme — hors tests automatiques.

Usage (réseau, à lancer manuellement) :
  python tools/geopf_geocode_probe.py --address "1 Place Jean Catelas, 80000 Amiens"
  python tools/geopf_geocode_probe.py --lat 49.8941 --lon 2.2957
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.geocoding import GeocodingError, GeopfGeocodingService  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe géocodage Géoplateforme IGN")
    parser.add_argument("--address", help="Adresse texte (géocodage direct)")
    parser.add_argument("--lat", type=float, help="Latitude (géocodage inverse)")
    parser.add_argument("--lon", type=float, help="Longitude (géocodage inverse)")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--json", action="store_true", help="Sortie JSON compacte")
    args = parser.parse_args(argv)

    has_address = bool(args.address and str(args.address).strip())
    has_coords = args.lat is not None or args.lon is not None
    if has_address == has_coords:
        parser.error("Indiquez soit --address, soit --lat et --lon.")
    if has_coords and (args.lat is None or args.lon is None):
        parser.error("--lat et --lon sont tous les deux requis.")

    service = GeopfGeocodingService(timeout=args.timeout)
    try:
        if has_address:
            result = service.geocode(args.address)
            mode = "search"
        else:
            result = service.reverse(args.lat, args.lon)
            mode = "reverse"
    except GeocodingError as exc:
        print(f"ERREUR: {exc}", file=sys.stderr)
        return 1

    payload = {
        "mode": mode,
        "latitude": result.latitude,
        "longitude": result.longitude,
        "label": result.label,
        "address": result.address,
        "city": result.city,
        "postcode": result.postcode,
        "citycode": result.citycode,
        "score": result.score,
        "source": result.source,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"mode: {mode}")
        print(f"label: {result.label or '—'}")
        print(f"city: {result.city or '—'}  postcode: {result.postcode or '—'}")
        print(f"citycode (INSEE): {result.citycode or '—'}")
        print(f"lat/lon: {result.latitude}, {result.longitude}")
        print(f"score: {result.score}  source: {result.source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
