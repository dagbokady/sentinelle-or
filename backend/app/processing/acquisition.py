"""Acquisition des images Sentinel-2 et Landsat de la zone pilote.

Trois fournisseurs, tous derrière la même interface `Provider` (search / download), ce qui
isole le pipeline des changements d'API :

* EarthSearchProvider — Sentinel-2 L2A en COG public (AWS / Element84), SANS COMPTE. C'est le
  fournisseur par défaut : il lit uniquement la fenêtre des zones surveillées (AOI).
* CopernicusProvider — Sentinel-2 L2A via Copernicus Data Space (OData), avec identifiants.
* USGSProvider      — Landsat 8/9 Collection 2 Level-2 via l'API USGS M2M, avec identifiants.

« Même grille + masque nuages » (tâche dédiée) est assuré ici :
  - chaque AOI a une grille UTM fixe à 10 m (`EarthSearchProvider.grid`) : toutes ses images
    sont parfaitement superposables, quelle que soit la tuile MGRS d'origine — c'est la
    « même emprise » qui rend le ΔNDVI possible ;
  - les pixels nuageux / ombres / cirrus sont masqués à l'acquisition (bande SCL pour
    Sentinel-2, bits QA_PIXEL pour Landsat) et écrits à 0 (nodata) dans le GeoTIFF normalisé.

Sortie normalisée (quel que soit le capteur) : GeoTIFF 4 bandes uint16 = réflectance × 10 000,
nodata = 0, dans l'ordre RED, NIR, GREEN, BLUE, rangé dans data/images/<satellite>/<AAAA-MM-JJ>/.

Usage CLI :
    python -m app.processing.acquisition            # un cycle d'acquisition
"""
from __future__ import annotations

import argparse
import logging
import re
import shutil
import tempfile
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.windows import from_bounds as win_from_bounds
from shapely.geometry import Polygon, box, shape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_config, get_settings, resolve_path
from app.models import Image
from app.utils.database import session_scope
from app.utils.geo_utils import WGS84, reproject_geom, utm_crs_for

log = logging.getLogger("sentinelle.acquisition")


@dataclass
class ProductMeta:
    satellite: str           # "sentinel2" | "landsat"
    product_id: str
    tile_id: str
    acquisition_date: datetime
    cloud_cover: float
    footprint: Polygon       # WGS84
    extra: dict


class Provider(ABC):
    satellite: str

    @abstractmethod
    def search(self, bbox: list[float], start: datetime, end: datetime, max_cloud: float, limit: int) -> list[ProductMeta]: ...

    @abstractmethod
    def download(self, product: ProductMeta, dest: Path) -> Path:
        """Télécharge le produit et renvoie le chemin du GeoTIFF normalisé."""


class SkipProduct(Exception):
    """Produit volontairement ignoré (ex. zone trop nuageuse) — pas une erreur."""


def storage_path(product: ProductMeta) -> Path:
    root = resolve_path(get_config()["acquisition"]["storage_dir"])
    day = product.acquisition_date.strftime("%Y-%m-%d")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", product.product_id)[:120]
    return root / product.satellite / day / f"{safe}.tif"


def write_normalized(dest: Path, bands: dict[str, np.ndarray], profile: dict, invalid: np.ndarray | None,
                     tags: dict) -> Path:
    """Empile RED/NIR/GREEN/BLUE (réflectance float) en GeoTIFF uint16 × 10 000, nodata 0.

    `invalid` (pixels nuageux / ombres) est écrit à 0 : le masque nuages est appliqué ici,
    une fois pour toutes, de sorte que tout le reste du pipeline ignore ces pixels.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    out = np.zeros((4, *bands["RED"].shape), dtype=np.uint16)
    for i, name in enumerate(["RED", "NIR", "GREEN", "BLUE"]):
        refl = bands[name]
        arr = np.clip(np.round(refl * 10_000), 1, 65535).astype(np.uint16)
        arr[~np.isfinite(refl) | (refl <= 0)] = 0
        if invalid is not None:
            arr[invalid] = 0
        out[i] = arr
    profile = {**profile, "driver": "GTiff", "count": 4, "dtype": "uint16", "nodata": 0,
               "compress": "deflate", "tiled": True, "blockxsize": 512, "blockysize": 512, "BIGTIFF": "IF_SAFER"}
    with rasterio.open(dest, "w", **profile) as dst:
        dst.write(out)
        for i, name in enumerate(["RED", "NIR", "GREEN", "BLUE"], start=1):
            dst.set_band_description(i, name)
        dst.update_tags(**{k: str(v) for k, v in tags.items()})
        dst.build_overviews([2, 4, 8, 16], Resampling.average)
    return dest


# --------------------------------------------------------------------------- #
# Sentinel-2 L2A — COG publics Earth Search (Element84 / AWS Open Data), sans compte
# --------------------------------------------------------------------------- #
GDAL_HTTP_ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", AWS_NO_SIGN_REQUEST="YES",
                     CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_HTTP_MAX_RETRY="3", GDAL_HTTP_RETRY_DELAY="2",
                     GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES", GDAL_HTTP_MULTIPLEX="YES", GDAL_HTTP_VERSION="2",
                     VSI_CACHE="TRUE", GDAL_INGESTED_BYTES_AT_OPEN="65536")


class EarthSearchProvider(Provider):
    """Lit uniquement les fenêtres des zones surveillées (AOI) dans les COG Sentinel-2 L2A.

    Chaque AOI a sa propre grille fixe (UTM, 10 m) : toutes ses images sont parfaitement
    superposables, même si elles proviennent de tuiles MGRS ou de zones UTM différentes.
    `tile_id` = identifiant de l'AOI, ce qui sert de « même emprise » pour la référence ΔNDVI.
    """
    satellite = "sentinel2"
    ASSETS = {"RED": "red", "NIR": "nir", "GREEN": "green", "BLUE": "blue"}
    SCL_INVALID = {0, 1, 3, 8, 9, 10}  # nodata, saturé, ombre, nuages moyens/hauts, cirrus

    def __init__(self, aois: list[dict] | None = None):
        cfg = get_config()
        c = cfg["acquisition"]["sources"]["earthsearch"]
        self.stac, self.collection = c["stac_url"], c["collection"]
        self.tile_cloud_max, self.min_valid = c["tile_cloud_max"], c["min_valid_fraction"]
        self.aois = aois if aois is not None else cfg.get("aois", [])
        self.http = requests.Session()

    @staticmethod
    def grid(aoi: dict):
        """Grille UTM fixe à 10 m de l'AOI : (crs, transform, width, height). La MÊME pour
        toutes les dates, ce qui garantit la superposition parfaite exigée par le ΔNDVI."""
        w, s, e, n = aoi["bbox"]
        crs = utm_crs_for((w + e) / 2, (s + n) / 2)
        minx, miny, maxx, maxy = reproject_geom(box(w, s, e, n), WGS84, crs).bounds
        minx, maxy = np.floor(minx / 10) * 10, np.ceil(maxy / 10) * 10
        width, height = int(np.ceil((maxx - minx) / 10)), int(np.ceil((maxy - miny) / 10))
        return crs, from_origin(minx, maxy, 10, 10), width, height

    def search(self, bbox, start, end, max_cloud, limit):
        out: list[ProductMeta] = []
        for aoi in self.aois:
            area = box(*aoi["bbox"])
            r = self.http.post(f"{self.stac}/search", json={
                "collections": [self.collection], "bbox": aoi["bbox"], "limit": 200,
                "datetime": f"{start:%Y-%m-%dT%H:%M:%SZ}/{end:%Y-%m-%dT%H:%M:%SZ}",
                "query": {"eo:cloud_cover": {"lt": self.tile_cloud_max}}}, timeout=120)
            r.raise_for_status()
            by_day: dict[str, ProductMeta] = {}
            for it in r.json().get("features", []):
                if not shape(it["geometry"]).contains(area):  # une seule tuile MGRS doit couvrir toute la zone
                    continue
                p = it["properties"]
                when = datetime.fromisoformat(p["datetime"].replace("Z", "+00:00")).replace(tzinfo=None)
                day = when.strftime("%Y%m%d")
                meta = ProductMeta(self.satellite, f"{it['id']}_{aoi['id']}", aoi["id"], when,
                                   float(p.get("eo:cloud_cover", 0)), area,
                                   {"assets": {k: it["assets"][v]["href"] for k, v in {**self.ASSETS, "SCL": "scl"}.items()},
                                    "offset": 0 if p.get("earthsearch:boa_offset_applied")
                                    else (-1000 if float(p.get("s2:processing_baseline", 0) or 0) >= 4 else 0),
                                    "aoi": aoi})
                if day not in by_day or meta.cloud_cover < by_day[day].cloud_cover:
                    by_day[day] = meta
            out.extend(sorted(by_day.values(), key=lambda m: m.acquisition_date)[-limit:] if limit else by_day.values())
        return out

    def download(self, product, dest):
        crs, transform, width, height = self.grid(product.extra["aoi"])
        bounds = (transform.c, transform.f - height * 10, transform.c + width * 10, transform.f)
        off = product.extra["offset"]

        def fetch(item):
            key, href = item
            rs = Resampling.nearest if key == "SCL" else Resampling.bilinear
            with rasterio.Env(**GDAL_HTTP_ENV), rasterio.open(href) as src:
                win = win_from_bounds(*bounds, src.transform)
                inside = (win.col_off >= 0 and win.row_off >= 0 and win.col_off + win.width <= src.width
                          and win.row_off + win.height <= src.height)
                if src.crs == crs and inside:  # cas courant : lecture fenêtrée directe (quelques blocs COG)
                    arr = src.read(1, window=win, out_shape=(height, width), resampling=rs)
                else:  # AOI à cheval sur deux zones UTM : reprojection à la volée sur la grille fixe
                    with WarpedVRT(src, crs=crs, transform=transform, width=width, height=height, resampling=rs) as vrt:
                        arr = vrt.read(1)
            return key, (arr if key == "SCL" else np.where(arr == 0, np.nan, (arr.astype(np.float32) + off) / 10_000))

        assets = dict(product.extra["assets"])
        # masque nuages d'abord (bande SCL à 20 m, légère) : abandon précoce si la zone est trop nuageuse
        _, scl = fetch(("SCL", assets.pop("SCL")))
        invalid = np.isin(scl, list(self.SCL_INVALID))
        valid = float((~invalid).mean())
        if valid < self.min_valid:
            raise SkipProduct(f"zone trop nuageuse ({valid:.0%} de pixels valides)")
        with ThreadPoolExecutor(max_workers=4) as pool:
            bands = dict(pool.map(fetch, assets.items()))
        valid = float((~invalid & np.isfinite(bands["RED"])).mean())
        product.cloud_cover = round(100 * (1 - valid), 1)  # couverture nuageuse SUR la zone
        return write_normalized(dest, bands, {"crs": crs, "transform": transform, "width": width, "height": height},
                                invalid, {"SATELLITE": "sentinel2", "PRODUCT": product.product_id,
                                          "TILE": product.tile_id, "ACQUISITION": product.acquisition_date.isoformat()})


# --------------------------------------------------------------------------- #
# Sentinel-2 L2A — Copernicus Data Space Ecosystem (OData), avec compte
# --------------------------------------------------------------------------- #
class CopernicusProvider(Provider):
    satellite = "sentinel2"
    BANDS = {"RED": "B04_10m", "NIR": "B08_10m", "GREEN": "B03_10m", "BLUE": "B02_10m"}
    SCL_INVALID = {0, 1, 3, 8, 9, 10}

    def __init__(self):
        c = get_config()["acquisition"]["sources"]["sentinel2"]
        s = get_settings()
        self.odata, self.token_url, self.dl = c["odata_url"], c["token_url"], c["download_url"]
        self.user, self.password = s.copernicus_user, s.copernicus_password
        self._token: str | None = None
        self.http = requests.Session()

    def _auth(self) -> dict:
        if not self._token:
            if not self.user:
                raise RuntimeError("COPERNICUS_USER / COPERNICUS_PASSWORD non configurés")
            r = self.http.post(self.token_url, data={"grant_type": "password", "client_id": "cdse-public",
                                                     "username": self.user, "password": self.password}, timeout=60)
            r.raise_for_status()
            self._token = r.json()["access_token"]
        return {"Authorization": f"Bearer {self._token}"}

    def search(self, bbox, start, end, max_cloud, limit):
        w, s, e, n = bbox
        aoi = f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"
        flt = (
            "Collection/Name eq 'SENTINEL-2' "
            "and Attributes/OData.CSC.StringAttribute/any(a:a/Name eq 'productType' and a/OData.CSC.StringAttribute/Value eq 'S2MSI2A') "
            f"and OData.CSC.Intersects(area=geography'SRID=4326;{aoi}') "
            f"and ContentDate/Start gt {start:%Y-%m-%dT%H:%M:%S.000Z} and ContentDate/Start lt {end:%Y-%m-%dT%H:%M:%S.000Z} "
            f"and Attributes/OData.CSC.DoubleAttribute/any(a:a/Name eq 'cloudCover' and a/OData.CSC.DoubleAttribute/Value lt {max_cloud})"
        )
        r = self.http.get(f"{self.odata}/Products", params={
            "$filter": flt, "$orderby": "ContentDate/Start desc", "$top": limit, "$expand": "Attributes"}, timeout=120)
        r.raise_for_status()
        out = []
        for p in r.json().get("value", []):
            attrs = {a["Name"]: a.get("Value") for a in p.get("Attributes", [])}
            tile = re.search(r"_T(\d{2}[A-Z]{3})_", p["Name"])
            out.append(ProductMeta(
                satellite=self.satellite, product_id=p["Name"].removesuffix(".SAFE"),
                tile_id=tile.group(1) if tile else "", cloud_cover=float(attrs.get("cloudCover", 0)),
                acquisition_date=datetime.fromisoformat(p["ContentDate"]["Start"].replace("Z", "+00:00")).replace(tzinfo=None),
                footprint=shape(p["GeoFootprint"]) if p.get("GeoFootprint") else box(*bbox),
                extra={"id": p["Id"], "name": p["Name"], "baseline": attrs.get("processingBaseline")},
            ))
        return out

    def _nodes(self, path: str) -> list[dict]:
        r = self.http.get(f"{self.dl}/{path}/Nodes", headers=self._auth(), timeout=120)
        r.raise_for_status()
        return r.json().get("result", [])

    def _fetch(self, path: str, dest: Path):
        with self.http.get(f"{self.dl}/{path}/$value", headers=self._auth(), stream=True, timeout=600) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                shutil.copyfileobj(r.raw, f, length=1 << 20)

    def download(self, product, dest):
        pid, name = product.extra["id"], product.extra["name"]
        root = f"Products({pid})/Nodes({name})/Nodes(GRANULE)"
        granule = self._nodes(root)[0]["Name"]
        img = f"{root}/Nodes({granule})/Nodes(IMG_DATA)"
        files10 = {n["Name"]: n for n in self._nodes(f"{img}/Nodes(R10m)")}
        files20 = {n["Name"]: n for n in self._nodes(f"{img}/Nodes(R20m)")}
        offset = -1000 if float(product.extra.get("baseline") or 0) >= 4.0 else 0

        with tempfile.TemporaryDirectory() as tmp:
            local = {}
            for band, suffix in self.BANDS.items():
                fname = next(n for n in files10 if n.endswith(f"_{suffix}.jp2"))
                local[band] = Path(tmp) / fname
                self._fetch(f"{img}/Nodes(R10m)/Nodes({fname})", local[band])
            scl_name = next(n for n in files20 if n.endswith("_SCL_20m.jp2"))
            scl_path = Path(tmp) / scl_name
            self._fetch(f"{img}/Nodes(R20m)/Nodes({scl_name})", scl_path)

            bands = {}
            with rasterio.open(local["RED"]) as ref:
                profile = {"crs": ref.crs, "transform": ref.transform, "width": ref.width, "height": ref.height}
            for band, path in local.items():
                with rasterio.open(path) as src:
                    dn = src.read(1).astype(np.float32)
                    bands[band] = np.where(dn == 0, np.nan, (dn + offset) / 10_000)
            with rasterio.open(scl_path) as src:  # 20 m → 10 m (plus proche voisin)
                scl = src.read(1, out_shape=(profile["height"], profile["width"]), resampling=Resampling.nearest)
            invalid = np.isin(scl, list(self.SCL_INVALID))
            return write_normalized(dest, bands, profile, invalid,
                                    {"SATELLITE": "sentinel2", "PRODUCT": product.product_id, "TILE": product.tile_id})


# --------------------------------------------------------------------------- #
# Landsat-8/9 Collection 2 Level-2 — USGS M2M API
# --------------------------------------------------------------------------- #
class USGSProvider(Provider):
    satellite = "landsat"
    BANDS = {"RED": "SR_B4", "NIR": "SR_B5", "GREEN": "SR_B3", "BLUE": "SR_B2"}

    def __init__(self):
        c = get_config()["acquisition"]["sources"]["landsat"]
        s = get_settings()
        self.url, self.dataset = c["m2m_url"], c["dataset"]
        self.user, self.token = s.usgs_user, s.usgs_token
        self._key: str | None = None
        self.http = requests.Session()

    def _call(self, endpoint: str, payload: dict):
        headers = {}
        if endpoint != "login-token":
            if not self._key:
                if not self.user:
                    raise RuntimeError("USGS_USER / USGS_TOKEN non configurés")
                self._key = self._call("login-token", {"username": self.user, "token": self.token})
            headers["X-Auth-Token"] = self._key
        r = self.http.post(f"{self.url}/{endpoint}", json=payload, headers=headers, timeout=180)
        r.raise_for_status()
        body = r.json()
        if body.get("errorCode"):
            raise RuntimeError(f"USGS M2M {endpoint}: {body['errorCode']} {body.get('errorMessage')}")
        return body["data"]

    def search(self, bbox, start, end, max_cloud, limit):
        w, s, e, n = bbox
        data = self._call("scene-search", {
            "datasetName": self.dataset, "maxResults": limit, "metadataType": "summary",
            "sceneFilter": {
                "spatialFilter": {"filterType": "mbr", "lowerLeft": {"latitude": s, "longitude": w},
                                  "upperRight": {"latitude": n, "longitude": e}},
                "acquisitionFilter": {"start": f"{start:%Y-%m-%d}", "end": f"{end:%Y-%m-%d}"},
                "cloudCoverFilter": {"min": 0, "max": max_cloud, "includeUnknown": False},
            }})
        out = []
        for sc in data.get("results", []):
            display = sc["displayId"]  # ex. LC09_L2SP_196055_20260110_20260112_02_T1
            pr = display.split("_")[2] if display.count("_") >= 3 else ""
            out.append(ProductMeta(
                satellite=self.satellite, product_id=display, tile_id=pr,
                acquisition_date=datetime.fromisoformat(sc["temporalCoverage"]["startDate"][:19]),
                cloud_cover=float(sc.get("cloudCover") or 0),
                footprint=shape(sc["spatialCoverage"]) if sc.get("spatialCoverage") else box(*bbox),
                extra={"entityId": sc["entityId"]}))
        return out

    def download(self, product, dest):
        opts = self._call("download-options", {"datasetName": self.dataset, "entityIds": [product.extra["entityId"]],
                                                "includeSecondaryFileGroups": True})
        wanted = {f"{product.product_id}_{b}.TIF": k for k, b in self.BANDS.items()}
        wanted[f"{product.product_id}_QA_PIXEL.TIF"] = "QA"
        downloads = []
        for o in opts:
            for sec in o.get("secondaryDownloads", []) or []:
                if sec.get("available") and sec.get("displayId") in wanted:
                    downloads.append({"entityId": sec["entityId"], "productId": sec["id"]})
        req = self._call("download-request", {"downloads": downloads, "label": "sentinelle-or"})
        urls = {d["displayId"] if "displayId" in d else Path(d["url"]).name: d["url"]
                for d in req.get("availableDownloads", [])}

        with tempfile.TemporaryDirectory() as tmp:
            local = {}
            for fname, key in wanted.items():
                url = urls.get(fname) or next((u for n, u in urls.items() if fname in u or n.endswith(fname)), None)
                if not url:
                    raise RuntimeError(f"Bande Landsat introuvable : {fname}")
                local[key] = Path(tmp) / fname
                with self.http.get(url, stream=True, timeout=600) as r:
                    r.raise_for_status()
                    with open(local[key], "wb") as f:
                        shutil.copyfileobj(r.raw, f, length=1 << 20)
            bands = {}
            for key in self.BANDS:
                with rasterio.open(local[key]) as src:
                    dn = src.read(1).astype(np.float32)
                    profile = {"crs": src.crs, "transform": src.transform, "width": src.width, "height": src.height}
                    bands[key] = np.where(dn == 0, np.nan, dn * 0.0000275 - 0.2)  # facteurs C2 L2
            with rasterio.open(local["QA"]) as src:
                qa = src.read(1)
            # bits QA_PIXEL : 0 fill, 1 dilated cloud, 3 cloud, 4 cloud shadow
            invalid = (qa & 0b11011) > 0
            return write_normalized(dest, bands, profile, invalid,
                                    {"SATELLITE": "landsat", "PRODUCT": product.product_id, "TILE": product.tile_id})


# --------------------------------------------------------------------------- #
# Cycle d'acquisition
# --------------------------------------------------------------------------- #
def get_providers() -> list[Provider]:
    """Fournisseurs actifs selon config.yml et la présence éventuelle d'identifiants."""
    src = get_config()["acquisition"]["sources"]
    s = get_settings()
    providers: list[Provider] = []
    if src.get("earthsearch", {}).get("enabled"):
        providers.append(EarthSearchProvider())  # sans compte
    if src["sentinel2"]["enabled"] and s.copernicus_user:
        providers.append(CopernicusProvider())
    if src["landsat"]["enabled"] and s.usgs_user:
        providers.append(USGSProvider())
    return providers


def run_acquisition(db: Session, providers: list[Provider] | None = None, now: datetime | None = None,
                    start: datetime | None = None, limit: int | None = None, commit_each: bool = False) -> list[Image]:
    """Recherche + téléchargement des nouvelles images ; enregistre chaque image (processed=False)."""
    cfg = get_config()["acquisition"]
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    start = start or now - timedelta(days=cfg["lookback_days"])
    limit = cfg["max_products_per_run"] if limit is None else limit
    created: list[Image] = []
    for provider in providers or get_providers():
        try:
            products = provider.search(cfg["bbox"], start, now, cfg["max_cloud_cover"], limit)
        except Exception as exc:  # une source en panne ne bloque pas l'autre
            log.error("Recherche %s échouée : %s", provider.satellite, exc)
            continue
        log.info("%s (%s) : %d produit(s) candidat(s)", provider.satellite, type(provider).__name__, len(products))
        for p in products:
            if not isinstance(provider, EarthSearchProvider) and p.cloud_cover >= cfg["max_cloud_cover"]:
                continue
            if db.scalar(select(Image.id).where(Image.product_id == p.product_id)):
                continue
            try:
                path = provider.download(p, storage_path(p))
            except SkipProduct as exc:
                log.info("Image %s ignorée : %s", p.product_id, exc)
                db.add(Image(satellite=p.satellite, product_id=p.product_id, tile_id=p.tile_id,
                             acquisition_date=p.acquisition_date, cloud_cover=None, bbox=p.footprint.envelope,
                             file_path=None, processed=True))
                db.flush()
                continue
            except Exception as exc:
                log.error("Téléchargement %s échoué : %s", p.product_id, exc)
                continue
            img = Image(satellite=p.satellite, product_id=p.product_id, tile_id=p.tile_id,
                        acquisition_date=p.acquisition_date, cloud_cover=p.cloud_cover,
                        bbox=p.footprint.envelope, file_path=str(path), processed=False)
            db.add(img)
            db.flush()
            if commit_each:
                db.commit()
            created.append(img)
            log.info("Image enregistrée : %s (%s, %.1f %% nuages)", p.product_id, p.acquisition_date, p.cloud_cover)
    return created


def main():
    from app.utils.database import init_db

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    argparse.ArgumentParser(description="Cycle d'acquisition Sentinelle-Or").parse_args()
    init_db()
    with session_scope() as db:
        imgs = run_acquisition(db)
        print(f"{len(imgs)} nouvelle(s) image(s)")


if __name__ == "__main__":
    main()
