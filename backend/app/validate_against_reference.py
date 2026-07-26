"""Score StreetScan detections against the authoritative Maale Hermon GIS.

Once the council supplies the official Buqata pole/lighting/telecom layers
(GeoJSON or CSV, ITM or WGS84), this matches our located assets to them by
nearest-neighbour within a radius and reports real precision/recall per layer —
the honest pilot metric, replacing manual sampling.

Usage:
    python -m app.validate_against_reference reference.geojson [--radius 15]
    python -m app.validate_against_reference reference.csv --radius 20

Reference formats accepted:
  * GeoJSON FeatureCollection of Points. Coordinates in ITM (EPSG:2039) if a
    matching crs is declared, else assumed WGS84 [lng,lat].
  * CSV with columns easting/northing (ITM) OR lat/lng (WGS84); an optional
    'layer' or 'asset_type' column enables per-layer scoring.
"""
from __future__ import annotations

import csv
import json
import math
import sys

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.entities import Asset
from app import gis_export


def _ref_points(path: str) -> list[tuple[float, float, str]]:
    """Return reference points as (easting_itm, northing_itm, layer)."""
    pts: list[tuple[float, float, str]] = []
    if path.lower().endswith(".geojson") or path.lower().endswith(".json"):
        doc = json.load(open(path, encoding="utf-8"))
        crs = json.dumps(doc.get("crs", {}))
        is_itm = "2039" in crs
        for f in doc.get("features", []):
            g = f.get("geometry") or {}
            if g.get("type") != "Point":
                continue
            x, y = g["coordinates"][:2]
            props = f.get("properties") or {}
            layer = props.get("layer") or props.get("official_layer") or ""
            if is_itm:
                pts.append((x, y, layer))
            else:  # coordinates are WGS84 [lng, lat]
                e, n = gis_export._to_itm(x, y)
                pts.append((e, n, layer))
    else:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                low = {k.lower(): v for k, v in row.items()}
                layer = low.get("layer") or low.get("official_layer") or low.get("asset_type") or ""
                if low.get("easting_itm") or low.get("easting"):
                    e = float(low.get("easting_itm") or low["easting"])
                    n = float(low.get("northing_itm") or low["northing"])
                    pts.append((e, n, layer))
                elif low.get("lat") and low.get("lng"):
                    pts.append((*gis_export._to_itm(float(low["lng"]), float(low["lat"])), layer))
    return pts


def _our_points() -> list[tuple[float, float, str, int]]:
    with SessionLocal() as db:
        assets = db.scalars(
            select(Asset).where(Asset.latitude.is_not(None), Asset.longitude.is_not(None))
        ).all()
        out = []
        for a in assets:
            e, n = gis_export._to_itm(a.longitude, a.latitude)
            out.append((e, n, gis_export.official_layer(a.asset_type), a.id))
        return out


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    path = sys.argv[1]
    radius = 15.0
    if "--radius" in sys.argv:
        radius = float(sys.argv[sys.argv.index("--radius") + 1])

    ref = _ref_points(path)
    ours = _our_points()
    print(f"reference points: {len(ref)} | our located assets: {len(ours)} | match radius: {radius}m\n")

    # A detection is a true positive if any reference point of the same official
    # layer sits within `radius`. Each reference point matches at most once.
    used = [False] * len(ref)
    tp = 0
    matched_layers: dict[str, list[int]] = {}
    for e, n, layer, aid in ours:
        best_i, best_d = -1, radius
        for i, (re, rn, rlayer) in enumerate(ref):
            if used[i] or (rlayer and layer and rlayer != layer):
                continue
            d = _dist((e, n), (re, rn))
            if d < best_d:
                best_d, best_i = d, i
        if best_i >= 0:
            used[best_i] = True
            tp += 1
        matched_layers.setdefault(layer, [0, 0])
        matched_layers[layer][1] += 1
        matched_layers[layer][0] += 1 if best_i >= 0 else 0

    precision = tp / len(ours) if ours else 0.0
    recall = tp / len(ref) if ref else 0.0
    print(f"TRUE POSITIVES: {tp}")
    print(f"PRECISION (our detections that hit a real asset): {precision:.1%}")
    print(f"RECALL    (real assets we found):                 {recall:.1%}\n")
    print("per official layer  (matched / our detections):")
    for lyr, (m, tot) in sorted(matched_layers.items(), key=lambda x: -x[1][1]):
        print(f"  {lyr}: {m}/{tot}  ({m/tot:.0%})" if tot else f"  {lyr}: 0/0")


if __name__ == "__main__":
    main()
