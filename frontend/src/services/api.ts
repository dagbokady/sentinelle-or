import type { Aoi, Comparison, Health, ImageDate, NdviPoint, ZoneDetail, ZoneFrame, ZoneSummary } from "../types";

const BASE = import.meta.env.VITE_API_URL ?? "";

async function get<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const url = new URL(BASE + path, window.location.origin);
  Object.entries(params ?? {}).forEach(([k, v]) => {
    if (v !== undefined && v !== "") url.searchParams.set(k, String(v));
  });
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`);
  return res.json() as Promise<T>;
}

export const api = {
  health: () => get<Health>("/api/health"),
  zones: (severity?: string, region?: string, minScore?: number) =>
    get<GeoJSON.FeatureCollection>("/api/zones", { severity, region, min_score: minScore }),
  zoneList: () => get<ZoneSummary[]>("/api/zones/list"),
  zone: (id: string) => get<ZoneDetail>(`/api/zones/${id}`),
  comparison: (id: string) => get<Comparison>(`/api/zones/${id}/comparison`),
  frames: (id: string) => get<ZoneFrame[]>(`/api/zones/${id}/frames`),
  ndviHistory: (id: string) => get<NdviPoint[]>(`/api/zones/${id}/ndvi-history`),
  imageryDates: () => get<ImageDate[]>("/api/imagery/dates"),
  aois: () => get<Aoi[]>("/api/aois"),
  tileUrl: (layer: "ndvi" | "truecolor", date?: string) =>
    `${BASE}/api/tiles/${layer}/{z}/{x}/{y}.png${date ? `?date=${date}` : ""}`,
};

/** Ajoute mode / taille / cadrage à une URL de vignette de zone (base contenant déjà ?img=…). */
export function frameSrc(base: string, o: { mode?: "truecolor" | "ndvi"; size?: number; extent?: number } = {}): string {
  const u = new URL(BASE + base, window.location.origin);
  if (o.mode === "ndvi") u.searchParams.set("mode", "ndvi");
  if (o.size) u.searchParams.set("size", String(o.size));
  if (o.extent) u.searchParams.set("extent_m", String(o.extent));
  return u.toString();
}

export const fmt = {
  date: (iso: string | null | undefined) =>
    iso ? new Date(iso).toLocaleDateString("fr-FR", { day: "2-digit", month: "short", year: "numeric" }) : "—",
  num: (v: number | null | undefined, d = 1) =>
    v === null || v === undefined ? "—" : v.toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d }),
  distance: (m: number | null | undefined) =>
    m === null || m === undefined ? "—" : m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toLocaleString("fr-FR", { maximumFractionDigits: 2 })} km`,
  coord: (lat: number, lon: number) =>
    `${Math.abs(lat).toFixed(5)}° ${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(5)}° ${lon >= 0 ? "E" : "O"}`,
};
