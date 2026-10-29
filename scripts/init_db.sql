-- Schéma initial Sentinelle-Or (PostgreSQL 16 + PostGIS 3)
-- Exécuté automatiquement par le conteneur db au premier démarrage.
-- Stockage des zones suspectes d'orpaillage détectées par ΔNDVI (khi²).

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- Images satellite normalisées (GeoTIFF 4 bandes), une par acquisition et par zone/tuile
CREATE TABLE IF NOT EXISTS images (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    satellite        VARCHAR(20)  NOT NULL CHECK (satellite IN ('sentinel2', 'landsat')),
    product_id       VARCHAR(200) UNIQUE,            -- identifiant fournisseur (dé-duplication)
    tile_id          VARCHAR(50),                    -- tuile MGRS / path-row / id de zone : même emprise
    acquisition_date TIMESTAMP    NOT NULL,
    cloud_cover      FLOAT,                           -- % de nuages SUR la zone (après masque)
    bbox             GEOMETRY(Polygon, 4326),
    file_path        VARCHAR(500),
    processed        BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMP    NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_images_tile_date ON images (satellite, tile_id, acquisition_date);
CREATE INDEX IF NOT EXISTS idx_images_bbox ON images USING GIST (bbox);

-- Zones suspectes (détections)
CREATE TABLE IF NOT EXISTS detections (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    image_id         UUID REFERENCES images(id) ON DELETE CASCADE,
    reference_image_id UUID REFERENCES images(id) ON DELETE SET NULL,
    site_id          UUID,                           -- regroupe les détections successives d'un même site
    geometry         GEOMETRY(Polygon, 4326) NOT NULL,
    delta_ndvi       FLOAT,                           -- ΔNDVI moyen de la zone
    chi2             FLOAT,                           -- écart khi² moyen à la normalité de la zone
    surface_ha       FLOAT,
    compacity        FLOAT,                           -- indice de Polsby-Popper
    river_distance_m FLOAT,
    nearest_river    VARCHAR(200),
    is_recidive      BOOLEAN DEFAULT FALSE,
    score            INTEGER CHECK (score BETWEEN 0 AND 100),
    score_details    JSONB,
    severity         VARCHAR(20) CHECK (severity IN ('urgente', 'moderee', 'surveillance')),
    commune          VARCHAR(100),
    region           VARCHAR(100),
    detection_date   TIMESTAMP NOT NULL,
    coordinates      GEOMETRY(Point, 4326),
    created_at       TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_detections_geom ON detections USING GIST (geometry);
CREATE INDEX IF NOT EXISTS idx_detections_date ON detections (detection_date);
CREATE INDEX IF NOT EXISTS idx_detections_severity ON detections (severity);
CREATE INDEX IF NOT EXISTS idx_detections_region ON detections (region);
CREATE INDEX IF NOT EXISTS idx_detections_site_id ON detections (site_id);

-- Historique NDVI par zone (courbe de la fiche)
CREATE TABLE IF NOT EXISTS ndvi_history (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    detection_id UUID REFERENCES detections(id) ON DELETE CASCADE,
    image_id     UUID REFERENCES images(id) ON DELETE SET NULL,
    ndvi_mean    FLOAT,
    capture_date TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ndvi_history_detection ON ndvi_history (detection_id);

-- Réseau hydrographique (OSM) : critère de proximité rivière
CREATE TABLE IF NOT EXISTS rivers (
    id       SERIAL PRIMARY KEY,
    osm_id   BIGINT,
    name     VARCHAR(200),
    geometry GEOMETRY(LineString, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rivers_geom ON rivers USING GIST (geometry);
