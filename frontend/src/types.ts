export type Severity = "urgente" | "moderee" | "surveillance";

export interface ZoneSummary {
  id: string;
  name: string;
  score: number;
  severity: Severity;
  surface_ha: number;
  delta_ndvi: number;
  chi2: number | null;
  river_distance_m: number | null;
  commune: string | null;
  region: string | null;
  detection_date: string;
  lat: number;
  lon: number;
  is_recidive: boolean;
  site_id?: string | null;
}

export interface Criterion {
  key: string;
  label: string;
  met: boolean;
  partial: number;
  weight: number;
  points: number;
  value: string | null;
}

export interface ImageInfo {
  id: string;
  satellite: string;
  acquisition_date: string;
  cloud_cover: number | null;
}

export interface ZoneDetail extends ZoneSummary {
  compacity: number;
  shape_ratio: number | null;
  nearest_river: string | null;
  ndvi_before: number | null;
  ndvi_after: number | null;
  median_drop: number | null;
  dispersion: number | null;
  criteria: Criterion[];
  bounds: [number, number, number, number];
  geometry: GeoJSON.Geometry;
  image: ImageInfo | null;
  reference_image: ImageInfo | null;
}

export interface ComparisonSide {
  date: string;
  satellite: string;
  url: string;
  ndvi_url: string;
  ndvi_mean: number | null;
}

export interface Comparison {
  before: ComparisonSide | null;
  after: ComparisonSide | null;
  bounds: number[];
}

export interface NdviPoint {
  date: string;
  ndvi_mean: number | null;
  image_id: string | null;
}

export interface Health {
  status: string;
  database: string;
  version: string;
  images: number;
  pending_images: number;
  detections: number;
}

export interface Aoi {
  id: string;
  name: string;
  bbox: [number, number, number, number];
}

export interface ImageDate {
  date: string;
  zones: string[];
  cloud_cover: number | null;
}

export interface ZoneFrame {
  date: string;
  datetime: string;
  satellite: string;
  cloud_cover: number | null;
  is_reference: boolean;
  is_detection: boolean;
  url: string;
  ndvi_url: string;
}

export const SEVERITY_LABEL: Record<Severity, string> = {
  urgente: "Urgente",
  moderee: "Modérée",
  surveillance: "Surveillance",
};

export const SEVERITY_COLOR: Record<Severity, string> = {
  urgente: "#d92d20",
  moderee: "#f79009",
  surveillance: "#12b76a",
};
