"""Calcul NDVI, ΔNDVI entre deux dates, détection du changement par khi², et vectorisation GeoJSON.

Format d'entrée normalisé (produit par acquisition.py, quel que soit le capteur) :
GeoTIFF 4 bandes uint16 = réflectance × 10 000, nodata = 0, dans l'ordre
    1: RED   2: NIR   3: GREEN   4: BLUE
Les pixels nuageux ont déjà été masqués à 0 à l'acquisition.

Détection du changement — pourquoi le khi² plutôt qu'un seuil fixe
------------------------------------------------------------------
Pour chaque pixel : d = NDVI_après − NDVI_avant. Un seuil fixe (« toute baisse > 0,30 est
suspecte ») confond une saison sèche, où la végétation baisse PARTOUT, avec une destruction.

À la place, on juge chaque pixel par rapport à la normalité de la zone au même moment :
  * médiane m des baisses d sur toute la zone (baisse typique, ex. -0,25 en saison sèche) ;
  * dispersion σ robuste autour de cette médiane (MAD × 1,4826, ex. 0,06) ;
  * écart réduit z = (d − m) / σ, et khi² = z² (1 degré de liberté).
Un pixel est retenu seulement s'il baisse BEAUCOUP plus que ses voisins (khi² > `chi2_threshold`,
soit 6,63 à 99 %) ET si sa baisse dépasse `min_abs_drop` en valeur absolue (garde-fou physique).

Exemple (m = -0,25, σ = 0,06) :
  * brousse desséchée, d = -0,33 → z = -1,33 → khi² = 1,78 < 6,63 → ignorée (fausse alerte évitée) ;
  * mine, d = -0,65 → z = -6,67 → khi² = 44 > 6,63 → retenue.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import geometry_mask, shapes, sieve
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window, from_bounds
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

BAND_RED, BAND_NIR, BAND_GREEN, BAND_BLUE = 1, 2, 3, 4
REFLECTANCE_SCALE = 10_000.0
NDVI_NODATA = -32768  # NDVI stocké en int16 × 10 000
NDVI_SCALE = 10_000.0
CHI2_P01 = 6.63       # seuil khi² à 1 ddl, p = 0.01


# --------------------------------------------------------------------------- #
# Étapes unitaires (fonctions pures, testables)
# --------------------------------------------------------------------------- #
def compute_ndvi(red: np.ndarray, nir: np.ndarray, nodata: float | None = 0) -> np.ndarray:
    """NDVI = (NIR − RED) / (NIR + RED), pixel par pixel. Les pixels invalides valent NaN."""
    red = red.astype(np.float32)
    nir = nir.astype(np.float32)
    denom = nir + red
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = (nir - red) / denom
    invalid = denom == 0
    if nodata is not None:
        invalid |= (red == nodata) | (nir == nodata)
    ndvi[invalid] = np.nan
    return np.clip(ndvi, -1.0, 1.0)


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """Dilatation binaire carrée (séparable) — sans dépendance scipy."""
    if radius <= 0 or not mask.any():
        return mask
    out = mask.copy()
    for axis in (0, 1):
        src = out.copy()
        for k in range(1, radius + 1):
            out[(slice(k, None),) if axis == 0 else (slice(None), slice(k, None))] |= \
                src[(slice(None, -k),) if axis == 0 else (slice(None), slice(None, -k))]
            out[(slice(None, -k),) if axis == 0 else (slice(None), slice(None, -k))] |= \
                src[(slice(k, None),) if axis == 0 else (slice(None), slice(k, None))]
    return out


def buffer_invalid(ndvi: np.ndarray, radius: int) -> np.ndarray:
    """Étend les zones invalides (nuages masqués) de `radius` pixels : le voile en bordure des
    nuages, non détecté par le masque, fait chuter le NDVI et créerait de fausses pertes."""
    if radius <= 0:
        return ndvi
    bad = dilate(np.isnan(ndvi), radius)
    return np.where(bad, np.nan, ndvi)


def compute_delta_ndvi(ndvi_current: np.ndarray, ndvi_reference: np.ndarray) -> np.ndarray:
    """ΔNDVI = NDVI_actuel − NDVI_référence (NaN si l'une des deux valeurs manque)."""
    return ndvi_current.astype(np.float32) - ndvi_reference.astype(np.float32)


def zone_baseline(delta: np.ndarray, min_dispersion: float = 0.03) -> tuple[float, float]:
    """Normalité de la zone : (médiane des baisses, dispersion robuste σ = MAD × 1.4826).

    Robuste = insensible aux quelques pixels de mine (les outliers qu'on cherche justement).
    `min_dispersion` est un plancher : sur une zone très homogène, σ ne doit pas tomber à 0.
    """
    d = delta[np.isfinite(delta)]
    if d.size == 0:
        return 0.0, min_dispersion
    median = float(np.median(d))
    mad = float(np.median(np.abs(d - median)))
    sigma = max(1.4826 * mad, min_dispersion)
    return median, sigma


def chi2_field(delta: np.ndarray, median: float, sigma: float) -> np.ndarray:
    """Champ khi² = ((ΔNDVI − médiane) / σ)² (NaN conservés)."""
    z = (delta - median) / sigma
    return z * z


def chi2_change_mask(delta: np.ndarray, chi2_threshold: float = CHI2_P01, min_abs_drop: float = 0.20,
                     min_dispersion: float = 0.03) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Masque des pixels anormalement dégradés par rapport à la zone.

    Retient un pixel si : khi² > seuil ET la variation est une BAISSE plus forte que la médiane
    (d < médiane) ET |d| ≥ min_abs_drop. Renvoie (masque, champ khi², médiane, σ).
    """
    median, sigma = zone_baseline(delta, min_dispersion)
    chi2 = chi2_field(delta, median, sigma)
    with np.errstate(invalid="ignore"):
        mask = (np.nan_to_num(chi2, nan=0.0) > chi2_threshold) & (delta < median) & (np.abs(delta) >= min_abs_drop)
    return mask, chi2, median, sigma


def threshold_mask(delta: np.ndarray, threshold: float = -0.30) -> np.ndarray:
    """Seuil fixe historique (ΔNDVI < seuil). Conservé pour comparaison ; khi² est préféré."""
    with np.errstate(invalid="ignore"):
        return np.nan_to_num(delta, nan=0.0) < threshold


def clean_mask(mask: np.ndarray, min_pixels: int = 4) -> np.ndarray:
    """Supprime les amas de moins de `min_pixels` pixels contigus (bruit, nuages résiduels)."""
    if min_pixels <= 1 or not mask.any():
        return mask
    sieved = sieve(mask.astype(np.uint8), size=min_pixels, connectivity=8).astype(bool) & mask
    filled = sieve(sieved.astype(np.uint8), size=min_pixels, connectivity=4).astype(bool)
    return sieved | (filled & ~sieved)


def vectorize_mask(mask: np.ndarray, transform, simplify_px: float = 0.0) -> list[BaseGeometry]:
    """Vectorise les zones contiguës du masque en polygones (CRS du raster)."""
    if not mask.any():
        return []
    pixel = abs(transform.a)
    polygons = []
    for geom, value in shapes(mask.astype(np.uint8), mask=mask, transform=transform, connectivity=8):
        if value != 1:
            continue
        poly = shape(geom)
        if simplify_px:
            poly = poly.simplify(pixel * simplify_px, preserve_topology=True)
        if not poly.is_valid:
            poly = poly.buffer(0)
        for part in getattr(poly, "geoms", [poly]):
            if part.geom_type == "Polygon" and not part.is_empty and part.area > 0:
                polygons.append(part)
    return polygons


def smooth_for_metrics(poly: BaseGeometry, pixel_size: float) -> BaseGeometry:
    """Lisse les marches d'escalier d'un pixel (sinon le périmètre est gonflé et toute zone paraît irrégulière)."""
    from shapely.geometry import Polygon

    outline = Polygon(poly.exterior) if poly.geom_type == "Polygon" else poly
    smooth = outline.simplify(pixel_size, preserve_topology=True)
    return smooth if smooth.is_valid and not smooth.is_empty else outline


def zonal_mean(raster: np.ndarray, geom: BaseGeometry, transform, scale: float = 1.0, nodata=None) -> float | None:
    """Moyenne des pixels d'un raster à l'intérieur d'un polygone (même CRS que le raster)."""
    minx, miny, maxx, maxy = geom.bounds
    win = from_bounds(minx, miny, maxx, maxy, transform).round_offsets().round_lengths()
    r0, c0 = max(int(win.row_off), 0), max(int(win.col_off), 0)
    r1 = min(int(win.row_off + win.height) + 1, raster.shape[0])
    c1 = min(int(win.col_off + win.width) + 1, raster.shape[1])
    if r1 <= r0 or c1 <= c0:
        return None
    sub = raster[r0:r1, c0:c1].astype(np.float32)
    sub_transform = rasterio.windows.transform(Window(c0, r0, c1 - c0, r1 - r0), transform)
    usable = ~np.isnan(sub) if nodata is None else (~np.isnan(sub) & (sub != nodata))
    for all_touched in (False, True):
        inside = geometry_mask([geom], out_shape=sub.shape, transform=sub_transform, invert=True,
                               all_touched=all_touched)
        valid = inside & usable
        if valid.any():
            return float(sub[valid].mean() / scale)
    return None


# --------------------------------------------------------------------------- #
# Lecture des images normalisées
# --------------------------------------------------------------------------- #
def read_ndvi(src, window: Window | None = None) -> np.ndarray:
    red = src.read(BAND_RED, window=window)
    nir = src.read(BAND_NIR, window=window)
    return compute_ndvi(red, nir, nodata=0)


def ndvi_file_path(image_path: str | Path) -> Path:
    p = Path(image_path)
    return p.with_name(p.stem + "_ndvi.tif")


def write_ndvi_raster(image_path: str | Path, block: int = 2048) -> Path:
    """Écrit le NDVI d'une image (int16 × 10 000) ; sert aux tuiles de la carte et à la fiche."""
    out = ndvi_file_path(image_path)
    with rasterio.open(image_path) as src:
        profile = src.profile.copy()
        profile.update(count=1, dtype="int16", nodata=NDVI_NODATA, compress="deflate",
                       tiled=True, blockxsize=256, blockysize=256, predictor=2)
        with rasterio.open(out, "w", **profile) as dst:
            for win in _windows(src.width, src.height, block):
                ndvi = read_ndvi(src, win)
                arr = np.where(np.isnan(ndvi), NDVI_NODATA, np.round(ndvi * NDVI_SCALE)).astype(np.int16)
                dst.write(arr, 1, window=win)
            dst.build_overviews([2, 4, 8, 16, 32], Resampling.average)
    return out


def _windows(width: int, height: int, block: int):
    for row in range(0, height, block):
        for col in range(0, width, block):
            yield Window(col, row, min(block, width - col), min(block, height - row))


# --------------------------------------------------------------------------- #
# Orchestration ΔNDVI (khi²) sur une paire d'images
# --------------------------------------------------------------------------- #
@dataclass
class ChangeZone:
    geometry: BaseGeometry          # CRS du raster courant
    delta_ndvi: float               # ΔNDVI moyen sur la zone
    chi2: float                     # khi² moyen (écart à la normalité) sur la zone
    ndvi_before: float | None
    ndvi_after: float | None
    red_after: float | None = None  # réflectance rouge moyenne après (sombre = brûlis probable)


@dataclass
class ChangeResult:
    crs: object
    transform: object
    zones: list[ChangeZone] = field(default_factory=list)
    changed_pixels: int = 0
    median_drop: float = 0.0        # normalité de la zone : baisse médiane
    dispersion: float = 0.0         # normalité de la zone : dispersion robuste σ


def detect_changes(current_path: str | Path, reference_paths, chi2_threshold: float = CHI2_P01,
                   min_abs_drop: float = 0.20, min_dispersion: float = 0.03, min_pixels: int = 4,
                   max_ndvi_after: float | None = None, cloud_buffer_px: int = 0) -> ChangeResult:
    """Compare une image à sa référence et renvoie les zones de perte de végétation anormale (khi²).

    `reference_paths` : une image ou une liste d'images antérieures (de la plus récente à la plus
    ancienne). La référence est un composite « dernière valeur valide par pixel » : un pixel
    nuageux dans l'image précédente est complété par l'image d'avant.
    `max_ndvi_after` : la zone doit être devenue sol nu / eau (NDVI après < seuil), ce qui écarte
    les simples baisses saisonnières. Les références sont rééchantillonnées à la volée si leur
    grille diffère (WarpedVRT) — en pratique elles partagent la grille fixe de l'AOI.

    La normalité (médiane, σ) est calculée sur TOUTE la zone valide, donc le khi² compare chaque
    pixel au reste de la zone au même moment.
    """
    refs_in = [reference_paths] if isinstance(reference_paths, (str, Path)) else list(reference_paths)
    with rasterio.open(current_path) as cur:
        n_cur = buffer_invalid(read_ndvi(cur), cloud_buffer_px)
        n_ref = np.full(n_cur.shape, np.nan, dtype=np.float32)
        opened = []
        try:
            for rp in refs_in:
                raw = rasterio.open(rp)
                opened.append(raw)
                same = (raw.crs == cur.crs and raw.transform == cur.transform
                        and raw.width == cur.width and raw.height == cur.height)
                src = raw if same else WarpedVRT(raw, crs=cur.crs, transform=cur.transform, width=cur.width,
                                                 height=cur.height, resampling=Resampling.bilinear, nodata=0)
                try:
                    r_ndvi = buffer_invalid(read_ndvi(src), cloud_buffer_px)
                finally:
                    if src is not raw:
                        src.close()
                n_ref = np.where(np.isnan(n_ref), r_ndvi, n_ref)  # dernière valeur valide par pixel
                if not np.isnan(n_ref).any():
                    break
            red_after = cur.read(BAND_RED)
        finally:
            for r in opened:
                r.close()

        delta = compute_delta_ndvi(n_cur, n_ref)
        mask, chi2, median, sigma = chi2_change_mask(delta, chi2_threshold, min_abs_drop, min_dispersion)
        if max_ndvi_after is not None:
            mask &= np.nan_to_num(n_cur, nan=1.0) < max_ndvi_after
        mask = clean_mask(mask, min_pixels)

        result = ChangeResult(crs=cur.crs, transform=cur.transform, changed_pixels=int(mask.sum()),
                              median_drop=median, dispersion=sigma)
        for poly in vectorize_mask(mask, cur.transform):
            d = zonal_mean(delta, poly, cur.transform)
            if d is None:
                continue
            result.zones.append(ChangeZone(
                geometry=poly,
                delta_ndvi=d,
                chi2=zonal_mean(chi2, poly, cur.transform) or 0.0,
                ndvi_before=zonal_mean(n_ref, poly, cur.transform),
                ndvi_after=zonal_mean(n_cur, poly, cur.transform),
                red_after=zonal_mean(red_after, poly, cur.transform, scale=REFLECTANCE_SCALE, nodata=0),
            ))
        return result


def extract_zones_geojson(current_path: str | Path, reference_paths, **kwargs) -> dict:
    """Extrait les zones suspectes et les renvoie en FeatureCollection GeoJSON (WGS84).

    Combine la tâche « calculer le ΔNDVI » et la tâche « fixer le seuil (khi²) et extraire les
    zones suspectes en GeoJSON ». Chaque Feature porte son ΔNDVI, son khi², ses NDVI avant/après.
    """
    from app.utils.geo_utils import to_wgs84

    change = detect_changes(current_path, reference_paths, **kwargs)
    features = []
    for z in change.zones:
        features.append({
            "type": "Feature",
            "geometry": mapping(to_wgs84(z.geometry, change.crs)),
            "properties": {
                "delta_ndvi": round(z.delta_ndvi, 4),
                "chi2": round(z.chi2, 2),
                "ndvi_before": None if z.ndvi_before is None else round(z.ndvi_before, 4),
                "ndvi_after": None if z.ndvi_after is None else round(z.ndvi_after, 4),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {"median_drop": round(change.median_drop, 4), "dispersion": round(change.dispersion, 4),
                     "changed_pixels": change.changed_pixels},
    }


def mean_ndvi_in(image_path: str | Path, geom_wgs84: BaseGeometry, min_valid: float = 0.0) -> float | None:
    """NDVI moyen d'une image sur un polygone WGS84 (pour l'historique NDVI d'une zone)."""
    from app.utils.geo_utils import reproject_geom

    with rasterio.open(image_path) as src:
        geom = reproject_geom(geom_wgs84, "EPSG:4326", src.crs)
        minx, miny, maxx, maxy = geom.bounds
        win = from_bounds(minx, miny, maxx, maxy, src.transform).round_offsets().round_lengths()
        win = Window(win.col_off - 1, win.row_off - 1, win.width + 2, win.height + 2)
        try:
            win = win.intersection(Window(0, 0, src.width, src.height))
        except rasterio.errors.WindowError:
            return None
        if win.width < 1 or win.height < 1:
            return None
        ndvi = read_ndvi(src, win)
        wt = rasterio.windows.transform(win, src.transform)
        if min_valid > 0:
            inside = geometry_mask([geom], out_shape=ndvi.shape, transform=wt, invert=True, all_touched=True)
            if not inside.any() or (~np.isnan(ndvi[inside])).mean() < min_valid:
                return None
        return zonal_mean(ndvi, geom, wt)
