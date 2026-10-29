"""Modèles SQLAlchemy — miroir de scripts/init_db.sql.

Les géométries sont manipulées côté Python comme des objets Shapely (EPSG:4326).
`GeometryType` les stocke en GEOMETRY PostGIS en production, et en WKT dans SQLite
(tests / développement local sans PostGIS).

Table centrale : `detections` — une zone suspecte d'orpaillage, avec sa géométrie,
son ΔNDVI, ses métriques de forme, son score et son niveau d'alerte.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from shapely import wkb as shapely_wkb
from shapely import wkt as shapely_wkt
from shapely.geometry.base import BaseGeometry
from sqlalchemy import (JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, Uuid, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator, UserDefinedType

from app.utils.database import Base


class PostGISGeometry(UserDefinedType):
    """Colonne GEOMETRY PostGIS sans conversion implicite (EWKT en entrée, EWKB hex en sortie)."""
    cache_ok = True

    def __init__(self, geometry_type: str, srid: int):
        self.geometry_type, self.srid = geometry_type, srid

    def get_col_spec(self, **kw):
        return f"geometry({self.geometry_type},{self.srid})"


class GeometryType(TypeDecorator):
    impl = Text
    cache_ok = True

    def __init__(self, geometry_type: str = "GEOMETRY", srid: int = 4326):
        super().__init__()
        self.geometry_type = geometry_type
        self.srid = srid

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PostGISGeometry(self.geometry_type, self.srid))
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value: BaseGeometry | None, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return f"SRID={self.srid};{value.wkt}"  # geometry_in accepte l'EWKT
        return value.wkt

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            if isinstance(value, memoryview | bytes):
                return shapely_wkb.loads(bytes(value))
            return shapely_wkb.loads(value, hex=True)  # sortie texte de geometry = EWKB hex
        return shapely_wkt.loads(value)


JSONType = JSON().with_variant(JSONB(), "postgresql")


class Image(Base):
    """Une image satellite normalisée téléchargée (GeoTIFF 4 bandes), par tuile / zone surveillée."""
    __tablename__ = "images"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    satellite: Mapped[str] = mapped_column(String(20))              # 'sentinel2' | 'landsat'
    product_id: Mapped[str | None] = mapped_column(String(200), unique=True)
    tile_id: Mapped[str | None] = mapped_column(String(50))         # tuile MGRS / path-row / id de zone : même emprise
    acquisition_date: Mapped[datetime] = mapped_column(DateTime)
    cloud_cover: Mapped[float | None] = mapped_column(Float)
    bbox = mapped_column(GeometryType("POLYGON"), nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(500))
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Detection(Base):
    """Zone suspecte : perte de végétation brutale détectée par ΔNDVI (khi²) puis filtrée et notée."""
    __tablename__ = "detections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    image_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"))
    reference_image_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("images.id", ondelete="SET NULL"))
    site_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)  # détections successives d'un même site
    geometry = mapped_column(GeometryType("POLYGON"), nullable=False)
    delta_ndvi: Mapped[float | None] = mapped_column(Float)
    chi2: Mapped[float | None] = mapped_column(Float)              # écart khi² moyen de la zone à la normalité
    surface_ha: Mapped[float | None] = mapped_column(Float)
    compacity: Mapped[float | None] = mapped_column(Float)
    river_distance_m: Mapped[float | None] = mapped_column(Float)
    nearest_river: Mapped[str | None] = mapped_column(String(200))
    is_recidive: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[int | None] = mapped_column(Integer)
    score_details = mapped_column(JSONType, nullable=True)
    severity: Mapped[str | None] = mapped_column(String(20))        # 'urgente' | 'moderee' | 'surveillance'
    commune: Mapped[str | None] = mapped_column(String(100))
    region: Mapped[str | None] = mapped_column(String(100))
    detection_date: Mapped[datetime] = mapped_column(DateTime)
    coordinates = mapped_column(GeometryType("POINT"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    image: Mapped[Image | None] = relationship(foreign_keys=[image_id])
    reference_image: Mapped[Image | None] = relationship(foreign_keys=[reference_image_id])
    ndvi_history: Mapped[list[NdviHistory]] = relationship(
        back_populates="detection", cascade="all, delete-orphan", order_by="NdviHistory.capture_date"
    )


class NdviHistory(Base):
    """NDVI moyen de la zone à chaque date d'image disponible (courbe de la fiche)."""
    __tablename__ = "ndvi_history"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    detection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("detections.id", ondelete="CASCADE"))
    image_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("images.id", ondelete="SET NULL"))
    ndvi_mean: Mapped[float | None] = mapped_column(Float)
    capture_date: Mapped[datetime] = mapped_column(DateTime)

    detection: Mapped[Detection] = relationship(back_populates="ndvi_history")


class River(Base):
    """Réseau hydrographique (OSM) : sert à mesurer la proximité d'une rivière (critère de score)."""
    __tablename__ = "rivers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    osm_id: Mapped[int | None] = mapped_column(BigInteger)
    name: Mapped[str | None] = mapped_column(String(200))
    geometry = mapped_column(GeometryType("LINESTRING"), nullable=False)
