"""API qui sert les zones et les images avant/après.

* GET /api/zones                      → FeatureCollection GeoJSON (carte)
* GET /api/zones/{id}                 → fiche détaillée d'une zone
* GET /api/zones/{id}/comparison      → vignettes satellite avant / après (URLs)
* GET /api/zones/{id}/thumbnail/{which}.png → image PNG avant|après (vraie couleur ou NDVI)
* GET /api/zones/{id}/ndvi-history    → courbe NDVI de la zone
* GET /api/tiles/{layer}/{z}/{x}/{y}.png → tuiles raster NDVI / vraie couleur (fond daté)
* GET /api/imagery/dates, /api/aois   → métadonnées pour le sélecteur d'image et les zones surveillées
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import asc, desc, select
from sqlalchemy.orm import Session

from app.config import get_config
from app.models import Detection, Image
from app.schemas import (Comparison, ComparisonSide, Criterion, Health, ImageInfo, NdviPoint,
                         ZoneDetail, ZoneSummary)
from app.utils.database import get_db
from app.utils.geo_utils import geom_to_geojson
from app.utils.rendering import (EMPTY_TILE, LRUBytes, render_thumbnail, render_tile, tile_bounds_wgs84)

router = APIRouter(prefix="/api", tags=["zones"])
_tile_cache = LRUBytes()
MIN_TILE_ZOOM = 6
SORTS = {"score": Detection.score, "date": Detection.detection_date,
         "surface": Detection.surface_ha, "delta_ndvi": Detection.delta_ndvi}


def zone_code(d: Detection) -> str:
    return "SO-" + d.id.hex[:6].upper()


def zone_name(d: Detection) -> str:
    return f"{d.commune or 'Zone'} · {zone_code(d)}"


def get_detection(db: Session, zone_id) -> Detection:
    from fastapi import HTTPException

    d = db.get(Detection, zone_id)
    if d is None:
        raise HTTPException(404, "Zone introuvable")
    return d


def summary(d: Detection) -> ZoneSummary:
    c = d.coordinates
    return ZoneSummary(
        id=d.id, name=zone_name(d), score=d.score or 0, severity=d.severity or "surveillance",
        surface_ha=d.surface_ha or 0, delta_ndvi=d.delta_ndvi or 0, chi2=d.chi2,
        river_distance_m=d.river_distance_m, commune=d.commune, region=d.region,
        detection_date=d.detection_date, lat=c.y, lon=c.x, is_recidive=bool(d.is_recidive), site_id=d.site_id)


# --------------------------------------------------------------------------- #
# Liste GeoJSON (carte) + liste paginée
# --------------------------------------------------------------------------- #
def _filtered(severity: str | None, region: str | None, min_score: int | None):
    stmt = select(Detection)
    if severity:
        stmt = stmt.where(Detection.severity.in_([s.strip() for s in severity.split(",") if s.strip()]))
    if region:
        stmt = stmt.where(Detection.region == region)
    if min_score is not None:
        stmt = stmt.where(Detection.score >= min_score)
    return stmt


@router.get("/zones", summary="Zones suspectes au format GeoJSON (carte Leaflet)")
def zones_geojson(severity: str | None = None, region: str | None = None, min_score: int | None = None,
                  db: Session = Depends(get_db)):
    rows = db.scalars(_filtered(severity, region, min_score).order_by(Detection.score.asc())).all()
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature", "id": str(d.id), "geometry": geom_to_geojson(d.geometry),
            "properties": {"id": str(d.id), "code": zone_code(d), "name": zone_name(d), "score": d.score,
                           "severity": d.severity, "surface_ha": d.surface_ha, "delta_ndvi": d.delta_ndvi,
                           "chi2": d.chi2, "commune": d.commune, "region": d.region,
                           "detection_date": d.detection_date.isoformat(),
                           "lat": d.coordinates.y, "lon": d.coordinates.x, "is_recidive": bool(d.is_recidive)},
        } for d in rows],
    }


@router.get("/zones/list", response_model=list[ZoneSummary], summary="Liste des zones (tableau)")
def zones_list(sort: str = Query("score", pattern="^(score|date|surface|delta_ndvi)$"),
               order: str = Query("desc", pattern="^(asc|desc)$"), limit: int = Query(100, ge=1, le=500),
               db: Session = Depends(get_db)):
    direction = desc if order == "desc" else asc
    rows = db.scalars(select(Detection).order_by(direction(SORTS[sort]), desc(Detection.detection_date)).limit(limit)).all()
    return [summary(d) for d in rows]


# --------------------------------------------------------------------------- #
# Fiche d'une zone
# --------------------------------------------------------------------------- #
def _image_info(img: Image | None) -> ImageInfo | None:
    if img is None:
        return None
    return ImageInfo(id=img.id, satellite=img.satellite, acquisition_date=img.acquisition_date,
                     cloud_cover=img.cloud_cover)


def _criteria(d: Detection) -> list[Criterion]:
    cfg = get_config()
    details = (d.score_details or {}).get("criteria", {})
    values = {
        "delta_ndvi": f"ΔNDVI {d.delta_ndvi:+.2f}" + (f" · χ² {d.chi2:.1f}" if d.chi2 is not None else ""),
        "irregularity": f"Polsby-Popper {d.compacity:.2f}",
        "river_proximity": "—" if d.river_distance_m is None else f"{d.river_distance_m:,.0f} m".replace(",", " "),
        "recidive": "Oui" if d.is_recidive else "Non",
    }
    out = [Criterion(key=k, label=v["label"], met=v["met"], partial=v["partial"], weight=v["weight"],
                     points=v["points"], value=values.get(k)) for k, v in details.items()]
    f = cfg["filters"]
    out.append(Criterion(key="surface", label=f"Surface entre {f['min_surface_ha']} et {f['max_surface_ha']} ha",
                         met=f["min_surface_ha"] <= (d.surface_ha or 0) <= f["max_surface_ha"],
                         partial=1, weight=0, points=0, value=f"{d.surface_ha:.2f} ha"))
    return out


@router.get("/zones/{zone_id}", response_model=ZoneDetail, summary="Fiche détaillée d'une zone")
def zone_detail(zone_id: uuid.UUID, db: Session = Depends(get_db)):
    d = get_detection(db, zone_id)
    s = summary(d)
    details = d.score_details or {}
    return ZoneDetail(
        **s.model_dump(), compacity=d.compacity or 0, shape_ratio=details.get("shape_ratio"),
        nearest_river=d.nearest_river, ndvi_before=details.get("ndvi_before"), ndvi_after=details.get("ndvi_after"),
        median_drop=details.get("median_drop"), dispersion=details.get("dispersion"),
        criteria=_criteria(d), bounds=list(d.geometry.bounds), geometry=geom_to_geojson(d.geometry),
        image=_image_info(d.image), reference_image=_image_info(d.reference_image))


# --------------------------------------------------------------------------- #
# Images avant / après
# --------------------------------------------------------------------------- #
@router.get("/zones/{zone_id}/comparison", response_model=Comparison, summary="Vignettes satellite avant / après")
def comparison(zone_id: uuid.UUID, db: Session = Depends(get_db)):
    d = get_detection(db, zone_id)
    details = d.score_details or {}

    def side(img: Image | None, which: str, ndvi):
        if img is None:
            return None
        base = f"/api/zones/{d.id}/thumbnail/{which}.png?img={img.id.hex[:8]}"
        return ComparisonSide(date=img.acquisition_date, satellite=img.satellite, url=base,
                              ndvi_url=base + "&mode=ndvi", ndvi_mean=ndvi)

    return Comparison(before=side(d.reference_image, "before", details.get("ndvi_before")),
                      after=side(d.image, "after", details.get("ndvi_after")), bounds=list(d.geometry.bounds))


@router.get("/zones/{zone_id}/thumbnail/{which}.png", summary="Vignette PNG (vraie couleur ou NDVI)",
            response_class=Response, responses={200: {"content": {"image/png": {}}}})
def thumbnail(zone_id: uuid.UUID, which: str, mode: str = Query("truecolor", pattern="^(truecolor|ndvi)$"),
              size: int = Query(320, ge=64, le=1024),
              extent_m: float = Query(900, ge=200, le=6000, description="largeur minimale du cadrage, en mètres"),
              db: Session = Depends(get_db)):
    d = get_detection(db, zone_id)
    img = d.reference_image if which == "before" else d.image
    if img is None or not img.file_path or not Path(img.file_path).exists():
        return Response(status_code=404)
    png = render_thumbnail(img.file_path, d.geometry, mode=mode, size=size, min_extent_m=extent_m)
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/zones/{zone_id}/ndvi-history", response_model=list[NdviPoint], summary="Historique NDVI de la zone")
def ndvi_history(zone_id: uuid.UUID, db: Session = Depends(get_db)):
    d = get_detection(db, zone_id)
    return [NdviPoint(date=h.capture_date, ndvi_mean=h.ndvi_mean, image_id=h.image_id) for h in d.ndvi_history]


# --------------------------------------------------------------------------- #
# Tuiles raster (fond Sentinel-2 daté) + métadonnées
# --------------------------------------------------------------------------- #
def _latest_per_tile(db: Session, area, until: datetime | None = None) -> list[str]:
    latest: dict[tuple, Image] = {}
    stmt = select(Image).where(Image.processed.is_(True), Image.file_path.is_not(None)).order_by(desc(Image.acquisition_date))
    if until is not None:
        stmt = stmt.where(Image.acquisition_date <= until)
    for img in db.scalars(stmt):
        key = (img.satellite, img.tile_id)
        if key in latest or img.bbox is None or not img.bbox.intersects(area):
            continue
        latest[key] = img
    return [i.file_path for i in sorted(latest.values(), key=lambda i: (i.satellite != "landsat", i.acquisition_date))]


@router.get("/tiles/{layer}/{z}/{x}/{y}", summary="Tuile raster (ndvi | truecolor)", response_class=Response)
def layer_tile(layer: str, z: int, x: int, y: str,
               date: str | None = Query(None, description="AAAA-MM-JJ : image la plus récente à cette date"),
               db: Session = Depends(get_db)):
    from shapely.geometry import box

    if layer not in ("ndvi", "truecolor"):
        return Response(status_code=404)
    yy = int(y.removesuffix(".png"))
    headers = {"Cache-Control": "public, max-age=3600"}
    if z < MIN_TILE_ZOOM:
        return Response(EMPTY_TILE, media_type="image/png", headers=headers)
    area = box(*tile_bounds_wgs84(z, x, yy))
    until = datetime.fromisoformat(date) + timedelta(days=1) if date else None
    paths = _latest_per_tile(db, area, until)
    key = (layer, z, x, yy, tuple(paths))
    png = _tile_cache.get(key)
    if png is None:
        png = render_tile(paths, layer, z, x, yy) if paths else EMPTY_TILE
        _tile_cache.put(key, png)
    return Response(png, media_type="image/png", headers=headers)


@router.get("/imagery/dates", summary="Dates d'acquisition disponibles (sélecteur d'image)")
def imagery_dates(db: Session = Depends(get_db)):
    rows = db.execute(select(Image.acquisition_date, Image.tile_id, Image.cloud_cover)
                      .where(Image.processed.is_(True), Image.file_path.is_not(None))
                      .order_by(desc(Image.acquisition_date))).all()
    days: dict[str, dict] = {}
    for when, tile, cloud in rows:
        k = when.date().isoformat()
        e = days.setdefault(k, {"date": k, "zones": [], "cloud_cover": []})
        e["zones"].append(tile)
        if cloud is not None:
            e["cloud_cover"].append(cloud)
    return [{"date": k, "zones": v["zones"],
             "cloud_cover": round(sum(v["cloud_cover"]) / len(v["cloud_cover"]), 1) if v["cloud_cover"] else None}
            for k, v in days.items()]


@router.get("/aois", summary="Zones surveillées (config.yml › aois)")
def aois():
    return [{"id": a["id"], "name": a["name"], "bbox": a["bbox"]} for a in get_config().get("aois", [])]


@router.get("/health", response_model=Health, summary="État du système")
def health(db: Session = Depends(get_db)):
    from sqlalchemy import func, text

    from app import __version__

    try:
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:  # pragma: no cover
        db_status = f"erreur : {exc}"
    return Health(status="ok" if db_status == "ok" else "degraded", database=db_status, version=__version__,
                  images=db.scalar(select(func.count(Image.id))) or 0,
                  pending_images=db.scalar(select(func.count(Image.id)).where(Image.processed.is_(False))) or 0,
                  detections=db.scalar(select(func.count(Detection.id))) or 0)
