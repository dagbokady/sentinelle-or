"""Fonctions géospatiales génériques : projections, mesures métriques, localisation administrative."""
from __future__ import annotations

import json
import math
from functools import lru_cache

from pyproj import CRS, Transformer
from shapely.geometry import Point, box, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from app.config import get_config, resolve_path

WGS84 = CRS.from_epsg(4326)


def utm_crs_for(lon: float, lat: float) -> CRS:
    """CRS UTM adapté (la Côte d'Ivoire est à cheval sur les zones 29N et 30N)."""
    zone = int((lon + 180) // 6) + 1
    return CRS.from_epsg((32600 if lat >= 0 else 32700) + zone)


@lru_cache(maxsize=32)
def _transformer(src: str, dst: str) -> Transformer:
    return Transformer.from_crs(CRS.from_user_input(src), CRS.from_user_input(dst), always_xy=True)


def reproject_geom(geom: BaseGeometry, src_crs, dst_crs) -> BaseGeometry:
    src, dst = CRS.from_user_input(src_crs).to_string(), CRS.from_user_input(dst_crs).to_string()
    if src == dst:
        return geom
    return transform(_transformer(src, dst).transform, geom)


def to_wgs84(geom: BaseGeometry, src_crs) -> BaseGeometry:
    return reproject_geom(geom, src_crs, WGS84)


def to_metric(geom_wgs84: BaseGeometry) -> tuple[BaseGeometry, CRS]:
    """Projette une géométrie WGS84 dans la zone UTM de son centroïde (mesures en mètres)."""
    c = geom_wgs84.centroid
    crs = utm_crs_for(c.x, c.y)
    return reproject_geom(geom_wgs84, WGS84, crs), crs


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6_371_008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def expand_bbox_m(geom_wgs84: BaseGeometry, meters: float):
    """Emprise WGS84 élargie d'environ `meters` (préfiltre spatial grossier)."""
    minx, miny, maxx, maxy = geom_wgs84.bounds
    dlat = meters / 111_320
    dlon = meters / (111_320 * max(math.cos(math.radians((miny + maxy) / 2)), 0.1))
    return box(minx - dlon, miny - dlat, maxx + dlon, maxy + dlat)


def geom_to_geojson(geom: BaseGeometry) -> dict:
    return mapping(geom)


@lru_cache
def _localities() -> list[dict]:
    path = resolve_path(get_config()["data"]["localities_path"])
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def locate(point: Point) -> tuple[str | None, str | None]:
    """Commune la plus proche et région administrative (référentiel data/localities.json)."""
    best, best_d = None, float("inf")
    for loc in _localities():
        d = haversine_m(point.x, point.y, loc["lon"], loc["lat"])
        if d < best_d:
            best, best_d = loc, d
    if best is None:
        return None, None
    return best["commune"], best["region"]
