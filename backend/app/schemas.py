"""Schémas Pydantic des réponses de l'API."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class Criterion(BaseModel):
    key: str
    label: str
    met: bool
    partial: float
    weight: float
    points: float
    value: str | None = None


class ZoneSummary(BaseModel):
    id: uuid.UUID
    name: str
    score: int
    severity: str
    surface_ha: float
    delta_ndvi: float
    chi2: float | None
    river_distance_m: float | None
    commune: str | None
    region: str | None
    detection_date: datetime
    lat: float
    lon: float
    is_recidive: bool
    site_id: uuid.UUID | None = None


class ImageInfo(BaseModel):
    id: uuid.UUID
    satellite: str
    acquisition_date: datetime
    cloud_cover: float | None


class ZoneDetail(ZoneSummary):
    compacity: float
    shape_ratio: float | None
    nearest_river: str | None
    ndvi_before: float | None
    ndvi_after: float | None
    median_drop: float | None
    dispersion: float | None
    criteria: list[Criterion]
    bounds: list[float]  # [lon_min, lat_min, lon_max, lat_max]
    geometry: dict
    image: ImageInfo | None
    reference_image: ImageInfo | None


class ComparisonSide(BaseModel):
    date: datetime
    satellite: str
    url: str
    ndvi_url: str
    ndvi_mean: float | None


class Comparison(BaseModel):
    before: ComparisonSide | None
    after: ComparisonSide | None
    bounds: list[float]


class NdviPoint(BaseModel):
    date: datetime
    ndvi_mean: float | None
    image_id: uuid.UUID | None


class Health(BaseModel):
    status: str
    database: str
    version: str
    images: int
    pending_images: int
    detections: int
