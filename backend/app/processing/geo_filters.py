"""Filtres géographiques et métriques de zone : surface, compacité, distance rivière, récidive.

Élimination des faux positifs (champ labouré, brûlis…) : on écarte les zones trop petites,
trop grandes ou trop régulières, et les zones sombres (brûlis probable). Toutes les mesures
se font en mètres dans la projection UTM locale de la zone.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_config
from app.models import Detection, River
from app.utils.geo_utils import WGS84, expand_bbox_m, reproject_geom, to_metric, utm_crs_for


# --------------------------------------------------------------------------- #
# Métriques de forme (fonctions pures, géométries métriques)
# --------------------------------------------------------------------------- #
def surface_ha(geom_m: BaseGeometry) -> float:
    return geom_m.area / 10_000.0


def polsby_popper(geom_m: BaseGeometry) -> float:
    """Indice de compacité 4π·A / P² : 1 = cercle, → 0 pour une forme très irrégulière."""
    p = geom_m.length
    return 0.0 if p == 0 else 4 * math.pi * geom_m.area / (p * p)


def shape_ratio(geom_m: BaseGeometry) -> float:
    """Ratio périmètre² / surface (carré = 16, cercle ≈ 12,57)."""
    return float("inf") if geom_m.area == 0 else geom_m.length ** 2 / geom_m.area


@dataclass
class FilterResult:
    passed: bool
    surface_ha: float
    compacity: float
    shape_ratio: float
    reason: str | None = None


def apply_exclusion_filters(geom_m: BaseGeometry, cfg: dict | None = None,
                            shape_geom_m: BaseGeometry | None = None, red_after: float | None = None) -> FilterResult:
    """Critères d'exclusion : surface, forme, luminosité (brûlis).

    `shape_geom_m` : contour lissé utilisé pour les indices de forme (sinon `geom_m`).
    """
    f = (cfg or get_config())["filters"]
    sg = shape_geom_m if shape_geom_m is not None else geom_m
    s, pp, ratio = surface_ha(geom_m), polsby_popper(sg), shape_ratio(sg)
    reason = None
    if s < f["min_surface_ha"]:
        reason = f"surface {s:.2f} ha < {f['min_surface_ha']} ha"
    elif s > f["max_surface_ha"]:
        reason = f"surface {s:.2f} ha > {f['max_surface_ha']} ha"
    elif ratio <= f["min_shape_ratio"]:
        reason = f"forme régulière (P²/A = {ratio:.1f} ≤ {f['min_shape_ratio']})"
    elif red_after is not None and red_after < f.get("min_red_after", 0):
        reason = f"zone sombre (rouge {red_after:.3f}) : brûlis probable"
    return FilterResult(reason is None, s, pp, ratio, reason)


# --------------------------------------------------------------------------- #
# Contexte géographique (réseau hydrographique, historique)
# --------------------------------------------------------------------------- #
class RiverIndex:
    """Index spatial des cours d'eau d'une emprise, en coordonnées métriques."""

    def __init__(self, rivers: list[tuple[str | None, BaseGeometry]], crs):
        self.crs = crs
        self.names = [n for n, _ in rivers]
        self.geoms = [reproject_geom(g, WGS84, crs) for _, g in rivers]
        self.tree = STRtree(self.geoms) if self.geoms else None

    @classmethod
    def load(cls, db: Session, area_wgs84: BaseGeometry, margin_m: float = 5_000) -> RiverIndex:
        c = area_wgs84.centroid
        crs = utm_crs_for(c.x, c.y)
        env = expand_bbox_m(area_wgs84, margin_m)
        rows = _spatial_candidates(db, River, env)
        return cls([(r.name, r.geometry) for r in rows], crs)

    def nearest(self, geom_wgs84: BaseGeometry) -> tuple[float | None, str | None]:
        if self.tree is None:
            return None, None
        g = reproject_geom(geom_wgs84, WGS84, self.crs)
        idx = self.tree.nearest(g)
        return float(g.distance(self.geoms[idx])), self.names[idx]


def _spatial_candidates(db: Session, model, envelope_wgs84: BaseGeometry):
    """Préfiltre spatial : opérateur && (index GiST) sous PostGIS, filtre Shapely sinon."""
    if db.bind.dialect.name == "postgresql":
        stmt = select(model).where(model.geometry.op("&&")(func.ST_MakeEnvelope(*envelope_wgs84.bounds, 4326)))
        return list(db.scalars(stmt))
    return [r for r in db.scalars(select(model)) if r.geometry.intersects(envelope_wgs84)]


def river_distance_m(db: Session, geom_wgs84: BaseGeometry, index: RiverIndex | None = None):
    index = index or RiverIndex.load(db, geom_wgs84)
    return index.nearest(geom_wgs84)


def nearby_detections(db: Session, geom_wgs84: BaseGeometry, detection_date: datetime,
                      cfg: dict | None = None) -> list[tuple[float, Detection]]:
    """Détections des N derniers mois à moins de `recidive_buffer_m` mètres, triées par distance."""
    s = (cfg or get_config())["scoring"]
    buffer_m = s["recidive_buffer_m"]
    since = detection_date - timedelta(days=30.44 * s["recidive_months"])
    env = expand_bbox_m(geom_wgs84, buffer_m)
    geom_m, crs = to_metric(geom_wgs84)

    if db.bind.dialect.name == "postgresql":
        stmt = select(Detection).where(
            Detection.geometry.op("&&")(func.ST_MakeEnvelope(*env.bounds, 4326)),
            Detection.detection_date >= since, Detection.detection_date <= detection_date)
        candidates = list(db.scalars(stmt))
    else:
        stmt = select(Detection).where(Detection.detection_date >= since, Detection.detection_date <= detection_date)
        candidates = [d for d in db.scalars(stmt) if d.geometry.intersects(env)]

    out = []
    for d in candidates:
        dist = reproject_geom(d.geometry, WGS84, crs).distance(geom_m)
        if dist <= buffer_m:
            out.append((dist, d))
    return sorted(out, key=lambda t: t[0])
