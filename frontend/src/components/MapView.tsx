import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useMemo, useRef, useState } from "react";
import { GeoJSON, MapContainer, Pane, Rectangle, TileLayer, Tooltip, useMap, useMapEvents } from "react-leaflet";
import { api, fmt } from "../services/api";
import type { Aoi, ImageDate, Severity } from "../types";

// --- Tâche 1 : fond de carte Leaflet centré sur la zone pilote (Côte d'Ivoire) ---
const CI_CENTER: L.LatLngExpression = [7.55, -5.55];
const CI_BOUNDS = L.latLngBounds([4.3, -8.6], [10.7, -2.5]);
const POLYGON_MIN_ZOOM = 11;

// Style « preuve satellite » : remplissage brun-rouge semi-opaque, contour clair
const DET_FILL = "#7a2414";
const CURRENT = "#ff5a1f";

export interface MapFocus {
  bounds: [number, number, number, number]; // lon_min, lat_min, lon_max, lat_max
  key: number;
}

interface Props {
  severity: Severity[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  focus: MapFocus | null;
}

interface ZoneProps {
  id: string; code: string; name: string; score: number; severity: Severity;
  surface_ha: number; delta_ndvi: number; chi2: number | null; detection_date: string;
}

/** Recentre la carte + expose l'instance Leaflet + suit le zoom. */
function Controller({ focus, onView, mapRef }: {
  focus: MapFocus | null; onView: (z: number) => void; mapRef: React.MutableRefObject<L.Map | null>;
}) {
  const map = useMap();
  mapRef.current = map;
  useMapEvents({ moveend: () => onView(map.getZoom()) });
  useEffect(() => {
    onView(map.getZoom());
    const scale = L.control.scale({ metric: true, imperial: false, position: "bottomleft", maxWidth: 140 }).addTo(map);
    const ro = new ResizeObserver(() => map.invalidateSize({ pan: false }));
    ro.observe(map.getContainer());
    return () => { ro.disconnect(); scale.remove(); };
  }, [map]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!focus) return;
    const [x0, y0, x1, y1] = focus.bounds;
    map.flyToBounds(L.latLngBounds([y0, x0], [y1, x1]).pad(0.8), { maxZoom: 16, duration: 1.2 });
  }, [focus, map]);
  return null;
}

export default function MapView({ severity, selectedId, onSelect, focus }: Props) {
  const [data, setData] = useState<GeoJSON.FeatureCollection | null>(null);
  const [zoom, setZoom] = useState(7);
  const [aois, setAois] = useState<Aoi[]>([]);
  const [dates, setDates] = useState<ImageDate[]>([]);
  const [s2date, setS2date] = useState<string | null>(null);
  const [ndvi, setNdvi] = useState(false);
  const mapRef = useRef<L.Map | null>(null);

  // --- Tâche 8 : récupérer les zones de l'API et les afficher sur la carte ---
  useEffect(() => { api.zones().then(setData).catch(() => setData(null)); }, []);
  useEffect(() => {
    api.aois().then(setAois).catch(() => setAois([]));
    api.imageryDates().then(setDates).catch(() => setDates([]));
  }, []);

  const visible = useMemo(() => {
    if (!data) return [];
    return data.features.filter((f) => {
      const p = f.properties as ZoneProps;
      if (severity.length && !severity.includes(p.severity)) return false;
      return !s2date || p.detection_date.slice(0, 10) <= s2date;
    });
  }, [data, severity, s2date]);

  const fc = useMemo<GeoJSON.FeatureCollection>(() => ({ type: "FeatureCollection", features: visible }), [visible]);
  const geoKey = useMemo(() => visible.map((f) => f.id).join("|") + selectedId, [visible, selectedId]);

  const sevColor = (s: Severity) => (s === "urgente" ? "#d92d20" : s === "moderee" ? "#f79009" : "#12b76a");

  return (
    <div className="map-wrap">
      <MapContainer center={CI_CENTER} zoom={7} minZoom={6} maxZoom={18} zoomControl={false} maxBounds={CI_BOUNDS.pad(0.5)}>
        <Controller focus={focus} mapRef={mapRef} onView={setZoom} />
        {/* Fond haute résolution (imagerie Esri) */}
        <TileLayer url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
                   attribution="Imagerie © Esri, Maxar, Earthstar Geographics" maxNativeZoom={18} maxZoom={18} />
        {/* Fond Sentinel-2 daté (vraie couleur ou NDVI) servi par l'API */}
        {(s2date || ndvi) && (
          <Pane name="imagery" style={{ zIndex: 350 }}>
            <TileLayer key={`${ndvi}-${s2date}`} url={api.tileUrl(ndvi ? "ndvi" : "truecolor", s2date ?? undefined)}
                       minZoom={6} maxZoom={18} maxNativeZoom={16} opacity={ndvi ? 0.85 : 1}
                       attribution="Copernicus Sentinel-2 (ESA) via Earth Search" />
          </Pane>
        )}

        {/* Zones surveillées (AOI) */}
        {aois.map((a) => (
          <Rectangle key={a.id} bounds={[[a.bbox[1], a.bbox[0]], [a.bbox[3], a.bbox[2]]]} interactive={zoom < 11}
                     pathOptions={{ color: "#ffffff", weight: 1.5, dashArray: "6 6", fill: false, opacity: zoom < 13 ? 0.9 : 0.4 }}
                     eventHandlers={{ click: () => mapRef.current?.flyToBounds([[a.bbox[1], a.bbox[0]], [a.bbox[3], a.bbox[2]]], { duration: 1 }) }}>
            {zoom < 13 && <Tooltip permanent direction="top" className="aoi-label">{a.name}</Tooltip>}
          </Rectangle>
        ))}

        {/* Zones suspectes : polygones au-delà d'un certain zoom, pastilles sinon */}
        {zoom >= POLYGON_MIN_ZOOM ? (
          <GeoJSON key={geoKey} data={fc}
            style={(f) => {
              const p = f!.properties as ZoneProps;
              const current = p.id === selectedId;
              return { color: current ? CURRENT : "#ffffff", weight: current ? 3 : 1.2, opacity: 1,
                       fillColor: current ? CURRENT : DET_FILL, fillOpacity: current ? 0.35 : 0.45 };
            }}
            onEachFeature={(f, layer) => {
              const p = f.properties as ZoneProps;
              layer.bindTooltip(`<b>${p.code}</b> · ${fmt.date(p.detection_date)}<br/>${fmt.num(p.surface_ha, 2)} ha · score ${p.score}`,
                                { sticky: true, className: "det-tip" });
              layer.on("click", () => onSelect(p.id));
            }} />
        ) : (
          visible.map((f) => {
            const p = f.properties as ZoneProps;
            return (
              <Rectangle key={p.id} bounds={pointBox(p)} pathOptions={{ color: sevColor(p.severity), weight: 2, fillOpacity: 0.9 }}
                         eventHandlers={{ click: () => { onSelect(p.id); mapRef.current?.flyTo(center(f), 14, { duration: 1 }); } }}>
                <Tooltip direction="top">{p.name} — score {p.score}</Tooltip>
              </Rectangle>
            );
          })
        )}
      </MapContainer>

      {/* Contrôles carte */}
      <div className="map-overlay map-topbar">
        <div className="ctrl-group zoom-pair">
          <button aria-label="Zoom avant" onClick={() => mapRef.current?.zoomIn()}>+</button>
          <button aria-label="Zoom arrière" onClick={() => mapRef.current?.zoomOut()}>−</button>
        </div>
        <label className="imagery-select">
          <span className="sr-only">Image affichée</span>
          <select value={s2date ?? "hr"} onChange={(e) => setS2date(e.target.value === "hr" ? null : e.target.value)}>
            <option value="hr">Haute résolution (Esri)</option>
            <optgroup label="Sentinel-2 — 10 m">
              {dates.map((d) => (
                <option key={d.date} value={d.date}>
                  Sentinel-2 · {fmt.date(d.date)}{d.cloud_cover != null ? ` · ${Math.round(d.cloud_cover)} % nuages` : ""}
                </option>
              ))}
            </optgroup>
          </select>
        </label>
        <label className="ndvi-toggle">
          <input type="checkbox" checked={ndvi} onChange={(e) => setNdvi(e.target.checked)} /> NDVI
        </label>
      </div>

      <div className="map-overlay map-controls">
        <button className="ctrl-group" aria-label="Vue nationale" title="Vue nationale"
                onClick={() => mapRef.current?.flyToBounds(CI_BOUNDS, { duration: 1 })}>⌂</button>
      </div>
      {zoom < POLYGON_MIN_ZOOM && <div className="map-hint">Zoomez pour voir les contours des zones</div>}
    </div>
  );
}

/** Petit rectangle autour du centroïde d'une zone (vue nationale / régionale). */
function pointBox(p: { id: string }, half = 0.004): L.LatLngBoundsExpression {
  const pr = p as unknown as ZoneProps & { lat: number; lon: number };
  return [[pr.lat - half, pr.lon - half], [pr.lat + half, pr.lon + half]];
}
function center(f: GeoJSON.Feature): L.LatLngExpression {
  const p = f.properties as unknown as { lat: number; lon: number };
  return [p.lat, p.lon];
}
