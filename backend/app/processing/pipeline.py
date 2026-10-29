"""Orchestration du pipeline :

acquisition → NDVI → ΔNDVI (khi²) vs image de référence → extraction des zones
→ filtres géographiques → scoring → insertion en base.

Usage CLI :
    python -m app.processing.pipeline              # acquisition + traitement
    python -m app.processing.pipeline --no-acquire # traiter les images en attente
    python -m app.processing.pipeline --backfill   # récupérer l'historique puis traiter (base vide)
"""
from __future__ import annotations

import argparse
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_config
from app.models import Detection, Image, NdviHistory
from app.processing import geo_filters as gf
from app.processing.acquisition import EarthSearchProvider, run_acquisition
from app.processing.ndvi_processing import detect_changes, mean_ndvi_in, smooth_for_metrics, write_ndvi_raster
from app.processing.scoring import compute_score
from app.utils.database import init_db, session_scope
from app.utils.geo_utils import to_metric, to_wgs84, locate

log = logging.getLogger("sentinelle.pipeline")


@dataclass
class ImageReport:
    image_id: str
    zones: int = 0
    excluded: int = 0
    detections: int = 0
    baseline: bool = False
    seconds: float = 0.0


def find_references(db: Session, image: Image, depth: int = 1) -> list[Image]:
    """Dernières images exploitables de la même zone (même capteur, même tuile), récentes d'abord."""
    stmt = (select(Image)
            .where(Image.satellite == image.satellite, Image.tile_id == image.tile_id,
                   Image.acquisition_date < image.acquisition_date, Image.processed.is_(True),
                   Image.id != image.id, Image.file_path.is_not(None))
            .order_by(Image.acquisition_date.desc()).limit(depth))
    return list(db.scalars(stmt))


def _zones_extent(change):
    from shapely.ops import unary_union

    return unary_union([z.geometry for z in change.zones]).envelope.buffer(abs(change.transform.a) * 10)


def process_image(db: Session, image: Image, history_depth: int = 6) -> ImageReport:
    cfg = get_config()
    t0 = time.perf_counter()
    report = ImageReport(str(image.id))
    write_ndvi_raster(image.file_path)

    p = cfg["processing"]
    refs = find_references(db, image, p.get("reference_depth", 1))
    ref = refs[0] if refs else None
    if ref is None:
        log.info("Image %s : première image de la zone %s → référence", image.product_id, image.tile_id)
        image.processed = True
        report.baseline = True
        report.seconds = time.perf_counter() - t0
        return report

    change = detect_changes(image.file_path, [r.file_path for r in refs],
                            chi2_threshold=p["chi2_threshold"], min_abs_drop=p["min_abs_drop"],
                            min_dispersion=p.get("min_dispersion", 0.03), min_pixels=p["min_pixels"],
                            max_ndvi_after=p.get("max_ndvi_after"), cloud_buffer_px=p.get("cloud_buffer_px", 0))
    pixel = abs(change.transform.a)
    report.zones = len(change.zones)
    log.info("Image %s : normalité de la zone — baisse médiane %.3f, dispersion %.3f",
             image.product_id, change.median_drop, change.dispersion)

    rivers = gf.RiverIndex.load(db, to_wgs84(_zones_extent(change), change.crs)) if change.zones else None
    previous_images = list(db.scalars(
        select(Image).where(Image.satellite == image.satellite, Image.tile_id == image.tile_id,
                            Image.acquisition_date <= image.acquisition_date, Image.processed.is_(True),
                            Image.file_path.is_not(None))
        .order_by(Image.acquisition_date.desc()).limit(history_depth)))

    for zone in change.zones:
        geom_wgs = to_wgs84(zone.geometry, change.crs)   # contour au pixel près (preuve)
        geom_m, _ = to_metric(geom_wgs)
        shape_m = smooth_for_metrics(zone.geometry, pixel)  # contour lissé pour les indices de forme
        filt = gf.apply_exclusion_filters(geom_m, cfg, shape_geom_m=shape_m, red_after=zone.red_after)
        if not filt.passed:
            report.excluded += 1
            log.debug("Zone écartée : %s", filt.reason)
            continue

        dist, river_name = rivers.nearest(geom_wgs) if rivers else (None, None)
        near = gf.nearby_detections(db, geom_wgs, image.acquisition_date, cfg)
        recid = any(d.detection_date < image.acquisition_date for _, d in near)
        site_id = (near[0][1].site_id or near[0][1].id) if near else uuid.uuid4()
        sc = compute_score(zone.delta_ndvi, filt.compacity, dist, recid, cfg)
        centroid = geom_wgs.centroid
        commune, region = locate(centroid)

        det = Detection(
            image_id=image.id, reference_image_id=ref.id, site_id=site_id, geometry=geom_wgs,
            delta_ndvi=round(zone.delta_ndvi, 4), chi2=round(zone.chi2, 2), surface_ha=round(filt.surface_ha, 3),
            compacity=round(filt.compacity, 4), river_distance_m=None if dist is None else round(dist, 1),
            nearest_river=river_name, is_recidive=recid, score=sc.score, severity=sc.severity,
            score_details={"criteria": sc.details, "shape_ratio": round(filt.shape_ratio, 2),
                           "chi2": round(zone.chi2, 2), "median_drop": round(change.median_drop, 4),
                           "dispersion": round(change.dispersion, 4),
                           "ndvi_before": zone.ndvi_before, "ndvi_after": zone.ndvi_after,
                           "red_after": zone.red_after},
            commune=commune, region=region, detection_date=image.acquisition_date, coordinates=centroid,
        )
        db.add(det)
        db.flush()
        for past in reversed(previous_images):
            if past.id == image.id:
                continue
            db.add(NdviHistory(detection_id=det.id, image_id=past.id, capture_date=past.acquisition_date,
                               ndvi_mean=mean_ndvi_in(past.file_path, geom_wgs)))
        db.add(NdviHistory(detection_id=det.id, image_id=image.id, capture_date=image.acquisition_date,
                           ndvi_mean=zone.ndvi_after))
        report.detections += 1

    image.processed = True
    report.seconds = time.perf_counter() - t0
    log.info("Image %s : %d zone(s), %d écartée(s), %d détection(s) — %.1f s",
             image.product_id, report.zones, report.excluded, report.detections, report.seconds)
    return report


def process_pending(db: Session, tile_ids: list[str] | None = None) -> list[ImageReport]:
    stmt = select(Image).where(Image.processed.is_(False)).order_by(Image.acquisition_date)
    if tile_ids:
        stmt = stmt.where(Image.tile_id.in_(tile_ids))
    reports = []
    for img in db.scalars(stmt).all():
        try:
            reports.append(process_image(db, img))
            db.commit()
        except Exception:
            db.rollback()
            log.exception("Échec du traitement de l'image %s", img.product_id)
    return reports


def backfill(days: int = 365) -> None:
    """Premier démarrage : télécharge l'historique Sentinel-2 des zones surveillées puis le traite."""
    log.info("Base vide : récupération de %d jours d'historique Sentinel-2 sur les zones surveillées…", days)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with session_scope() as db:
        run_acquisition(db, [EarthSearchProvider()], now=now, start=now - timedelta(days=days), limit=0, commit_each=True)
    with session_scope() as db:
        process_pending(db)


def main():
    parser = argparse.ArgumentParser(description="Pipeline Sentinelle-Or")
    parser.add_argument("--no-acquire", action="store_true", help="ne traiter que les images en attente")
    parser.add_argument("--backfill", action="store_true", help="récupérer l'historique (base vide) puis traiter")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    init_db()
    if args.backfill:
        backfill(days=get_config()["acquisition"].get("backfill_days", 365))
        return
    with session_scope() as db:
        if not args.no_acquire:
            run_acquisition(db)
    with session_scope() as db:
        reports = process_pending(db)
    print(f"{len(reports)} image(s) traitée(s), {sum(r.detections for r in reports)} détection(s)")


if __name__ == "__main__":
    main()
