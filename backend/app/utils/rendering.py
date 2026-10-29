"""Rendu raster → PNG : tuiles XYZ (NDVI / vraie couleur) et vignettes avant/après."""
from __future__ import annotations

import io
import math
from collections import OrderedDict
from pathlib import Path

import numpy as np
import rasterio
import rasterio.transform
from PIL import Image as PILImage, ImageDraw
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import from_bounds
from shapely.geometry.base import BaseGeometry

from app.processing.ndvi_processing import BAND_BLUE, BAND_GREEN, BAND_RED, NDVI_NODATA, NDVI_SCALE, ndvi_file_path

TILE = 256
WEB_MERCATOR = "EPSG:3857"
ORIGIN = 20037508.342789244

# Palette NDVI fausse couleur : eau → sol nu → végétation dégradée → forêt dense
_NDVI_STOPS = [(-0.2, (37, 99, 235)), (0.0, (165, 120, 82)), (0.2, (214, 170, 96)),
               (0.4, (234, 220, 120)), (0.6, (120, 190, 80)), (0.8, (24, 120, 48)), (1.0, (6, 70, 30))]


def tile_bounds_3857(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    size = 2 * ORIGIN / (2 ** z)
    minx = -ORIGIN + x * size
    maxy = ORIGIN - y * size
    return minx, maxy - size, minx + size, maxy


def tile_bounds_wgs84(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    def lon(xx):
        return xx / 2 ** z * 360 - 180

    def lat(yy):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / 2 ** z))))

    return lon(x), lat(y + 1), lon(x + 1), lat(y)


def colorize_ndvi(ndvi: np.ndarray) -> np.ndarray:
    """NDVI float (NaN = vide) → RGBA uint8."""
    xs = np.array([s[0] for s in _NDVI_STOPS])
    rgba = np.zeros((*ndvi.shape, 4), dtype=np.uint8)
    v = np.nan_to_num(ndvi, nan=-9)
    for c in range(3):
        rgba[..., c] = np.interp(v, xs, [s[1][c] for s in _NDVI_STOPS]).astype(np.uint8)
    rgba[..., 3] = np.where(np.isnan(ndvi), 0, 220)
    return rgba


def truecolor(red: np.ndarray, green: np.ndarray, blue: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Composition vraie couleur (réflectance ×10 000) avec étirement et gamma."""
    rgb = np.stack([red, green, blue], axis=-1).astype(np.float32) / 10_000
    rgb = np.clip(rgb / 0.22, 0, 1) ** 0.75
    out = np.zeros((*red.shape, 4), dtype=np.uint8)
    out[..., :3] = (rgb * 255).astype(np.uint8)
    out[..., 3] = np.where(valid, 255, 0)
    return out


def to_png(rgba: np.ndarray) -> bytes:
    buf = io.BytesIO()
    PILImage.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=False)
    return buf.getvalue()


EMPTY_TILE = to_png(np.zeros((TILE, TILE, 4), dtype=np.uint8))


def render_tile(paths: list[str], layer: str, z: int, x: int, y: int) -> bytes:
    """Compose les rasters (du plus ancien au plus récent) sur une tuile XYZ 256 px."""
    bounds = tile_bounds_3857(z, x, y)
    out = np.zeros((TILE, TILE, 4), dtype=np.uint8)
    dst_transform = rasterio.transform.from_bounds(*bounds, TILE, TILE)
    for p in paths:
        src_path = ndvi_file_path(p) if layer == "ndvi" else Path(p)
        if not src_path.exists():
            continue
        with rasterio.open(src_path) as src, WarpedVRT(src, crs=WEB_MERCATOR, transform=dst_transform, width=TILE,
                                                        height=TILE, resampling=Resampling.bilinear) as vrt:
            if layer == "ndvi":
                arr = vrt.read(1).astype(np.float32)
                ndvi = np.where(arr <= NDVI_NODATA + 1, np.nan, arr / NDVI_SCALE)
                rgba = colorize_ndvi(ndvi)
            else:
                bands = vrt.read([BAND_RED, BAND_GREEN, BAND_BLUE])
                rgba = truecolor(bands[0], bands[1], bands[2], bands[0] > 0)
        m = rgba[..., 3] > 0
        out[m] = rgba[m]
    return to_png(out) if out[..., 3].any() else EMPTY_TILE


def render_thumbnail(image_path: str, geom_wgs84: BaseGeometry, mode: str = "truecolor", size: int = 320,
                     min_extent_m: float = 900, outline: bool = True) -> bytes:
    """Vignette centrée sur la zone (contour de l'alerte dessiné), en vraie couleur ou NDVI."""
    from app.utils.geo_utils import reproject_geom

    src_path = ndvi_file_path(image_path) if mode == "ndvi" else Path(image_path)
    with rasterio.open(src_path) as src:
        g = reproject_geom(geom_wgs84, "EPSG:4326", src.crs)
        cx, cy = g.centroid.x, g.centroid.y
        minx, miny, maxx, maxy = g.bounds
        half = max(maxx - minx, maxy - miny, min_extent_m) * 0.75
        bounds = (cx - half, cy - half, cx + half, cy + half)
        win = from_bounds(*bounds, src.transform)
        if mode == "ndvi":
            arr = src.read(1, window=win, out_shape=(size, size), boundless=True, fill_value=NDVI_NODATA,
                           resampling=Resampling.bilinear).astype(np.float32)
            rgba = colorize_ndvi(np.where(arr <= NDVI_NODATA + 1, np.nan, arr / NDVI_SCALE))
            rgba[..., 3] = np.where(rgba[..., 3] > 0, 255, 0)
        else:
            b = src.read([BAND_RED, BAND_GREEN, BAND_BLUE], window=win, out_shape=(3, size, size),
                         boundless=True, fill_value=0, resampling=Resampling.bilinear)
            rgba = truecolor(b[0], b[1], b[2], b[0] > 0)

    img = PILImage.fromarray(rgba, "RGBA")
    if outline:
        draw = ImageDraw.Draw(img)
        sx = size / (bounds[2] - bounds[0])
        polys = [g] if g.geom_type == "Polygon" else list(getattr(g, "geoms", []))
        for poly in polys:
            pts = [((px - bounds[0]) * sx, (bounds[3] - py) * sx) for px, py in poly.exterior.coords]
            w = max(2, round(size / 160))
            draw.line(pts + [pts[0]], fill=(255, 255, 255, 255), width=w + 2, joint="curve")
            draw.line(pts + [pts[0]], fill=(229, 57, 53, 255), width=w, joint="curve")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class LRUBytes:
    """Petit cache mémoire de PNG (tuiles fréquemment demandées)."""

    def __init__(self, capacity: int = 2048):
        self.capacity = capacity
        self.data: OrderedDict = OrderedDict()

    def get(self, key):
        if key in self.data:
            self.data.move_to_end(key)
            return self.data[key]
        return None

    def put(self, key, value: bytes):
        self.data[key] = value
        self.data.move_to_end(key)
        while len(self.data) > self.capacity:
            self.data.popitem(last=False)
