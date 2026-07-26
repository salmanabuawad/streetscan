"""Hand StreetScan detections back to the authoritative municipal GIS.

Buqata's infrastructure lives in the Maale Hermon GISNET system (ArcGIS,
EPSG:2039 / Israeli TM Grid) under fixed layer names. To be importable there,
our WGS84 detections are (a) grouped under those exact layer names and
(b) reprojected to ITM. See the maale-hermon-gis reference for the schema.
"""
from __future__ import annotations

import csv
import io

# StreetScan asset_type -> the official Maale Hermon GIS layer it belongs in.
# transformer/utility_pole ride the electricity layer and telecom_cabinet the
# telephone layer (the official GIS has no dedicated layer for those); the
# precise type is preserved in the asset_type attribute so nothing is lost.
OFFICIAL_LAYER = {
    "street_light":     "עמודי תאורה בוקעאתא",
    "electricity_pole": "חשמל בוקעאתא",
    "transformer":      "חשמל בוקעאתא",
    "utility_pole":     "חשמל בוקעאתא",
    "telecom_pole":     "טלפונים עמודים בוקעאתא",
    "telecom_cabinet":  "טלפונים עמודים בוקעאתא",
    "manhole":          "ביוב שוחות בוקעאתא",
    "sewer_cover":      "ביוב שוחות בוקעאתא",
}
ITM_EPSG = 2039

_transformer = None


def _to_itm(lng: float, lat: float) -> tuple[float, float]:
    """WGS84 (lng, lat) -> Israeli TM Grid (easting, northing), metres."""
    global _transformer
    if _transformer is None:
        from pyproj import Transformer
        _transformer = Transformer.from_crs(4326, ITM_EPSG, always_xy=True)
    e, n = _transformer.transform(lng, lat)
    return round(e, 2), round(n, 2)


def official_layer(asset_type: str) -> str:
    return OFFICIAL_LAYER.get(asset_type, "לא מסווג בוקעאתא")


def asset_rows(assets, candidate_id_of) -> list[dict]:
    """Flatten located assets into export rows (ITM + WGS84 + provenance).
    `candidate_id_of(asset)` links back to the detection frame."""
    rows = []
    for a in assets:
        if a.latitude is None or a.longitude is None:
            continue
        e, n = _to_itm(a.longitude, a.latitude)
        rows.append({
            "official_layer": official_layer(a.asset_type),
            "asset_type": a.asset_type,
            "easting_itm": e,
            "northing_itm": n,
            "lat": round(a.latitude, 7),
            "lng": round(a.longitude, 7),
            "source": a.source or "",
            "notes": a.notes or "",
            "candidate_id": candidate_id_of(a),
            "asset_id": a.id,
        })
    return rows


def rows_to_geojson(rows: list[dict]) -> dict:
    """FeatureCollection with ITM geometry (a named CRS) so ArcGIS drops it
    straight onto the Israeli grid — no client-side reprojection."""
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": f"urn:ogc:def:crs:EPSG::{ITM_EPSG}"}},
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["easting_itm"], r["northing_itm"]]},
            "properties": {k: v for k, v in r.items() if k not in ("easting_itm", "northing_itm")},
        } for r in rows],
    }


def rows_to_csv(rows: list[dict]) -> bytes:
    """UTF-8 CSV with a BOM so Excel renders the Hebrew layer names correctly."""
    buf = io.StringIO()
    cols = ["official_layer", "asset_type", "easting_itm", "northing_itm",
            "lat", "lng", "source", "candidate_id", "asset_id", "notes"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")
