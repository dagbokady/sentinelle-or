import { useEffect, useState } from "react";
import { api, fmt } from "../services/api";
import type { Comparison, NdviPoint, ZoneDetail as Zone } from "../types";
import { SEVERITY_LABEL } from "../types";

interface Props {
  zoneId: string;
  onClose: () => void;
  onShowOnMap: (bounds: [number, number, number, number]) => void;
}

type Tab = "preuves" | "analyse";

/** Tâche 9 : fiche détaillée d'une zone — coordonnées, surface, images avant/après, score. */
export default function ZoneDetail({ zoneId, onClose, onShowOnMap }: Props) {
  const [zone, setZone] = useState<Zone | null>(null);
  const [cmp, setCmp] = useState<Comparison | null>(null);
  const [history, setHistory] = useState<NdviPoint[]>([]);
  const [mode, setMode] = useState<"truecolor" | "ndvi">("truecolor");
  const [tab, setTab] = useState<Tab>("preuves");
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setError(null);
    Promise.all([api.zone(zoneId), api.comparison(zoneId), api.ndviHistory(zoneId)])
      .then(([z, c, h]) => { setZone(z); setCmp(c); setHistory(h); })
      .catch((e) => setError(String(e)));
  }, [zoneId]);

  const copyGps = () => {
    if (!zone) return;
    navigator.clipboard?.writeText(`${zone.lat.toFixed(6)}, ${zone.lon.toFixed(6)}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const downloadGeoJSON = () => {
    if (!zone) return;
    const fc = { type: "FeatureCollection", features: [{ type: "Feature", geometry: zone.geometry,
      properties: { reference: zone.name, date: zone.detection_date, surface_ha: zone.surface_ha,
                    score: zone.score, severity: zone.severity, delta_ndvi: zone.delta_ndvi, chi2: zone.chi2 } }] };
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(fc)], { type: "application/geo+json" }));
    a.download = `${zone.name.split("· ").pop()}.geojson`;
    a.click();
  };

  if (error) return <aside className="detail"><div className="error-box">{error}</div></aside>;
  if (!zone) return <aside className="detail"><div className="loading"><div className="spinner" /></div></aside>;
  const code = zone.name.split("· ").pop();

  return (
    <aside className="detail" aria-label="Fiche de la zone">
      <div className="ev-head">
        <div>
          <div className="ev-ref">Référence : {code}</div>
          <div className="ev-type">Perte de végétation · site suspect d'orpaillage</div>
          <div className="ev-coords">Lat : {zone.lat.toFixed(4)}, Long : {zone.lon.toFixed(4)}</div>
        </div>
        <button className="ev-close" onClick={onClose} aria-label="Fermer">✕</button>
      </div>

      <div className="ev-actions">
        <button className="pill-btn" onClick={downloadGeoJSON}>⬇ GeoJSON</button>
        <button className="pill-btn" onClick={() => onShowOnMap(zone.bounds)}>◎ Zoomer sur la zone</button>
      </div>

      <div className="ev-info">
        <div className="ev-row"><span>Niveau</span>
          <span className="ev-score">
            <span className={`score-badge bg-${zone.severity}`}>{zone.score}</span>
            <span className={`sev-pill sev-${zone.severity}`}>{SEVERITY_LABEL[zone.severity]}</span>
          </span>
        </div>
        <div className="ev-row"><span>Date de détection</span><span>{fmt.date(zone.detection_date)}</span></div>
        <div className="ev-row"><span>Localisation</span><span>Côte d'Ivoire, {zone.region}, {zone.commune}</span></div>
        <div className="ev-row"><span>Surface</span><span>{fmt.num(zone.surface_ha, 2)} ha</span></div>
        <div className="ev-row"><span>Cours d'eau</span>
          <span>{zone.river_distance_m == null ? "aucun à proximité" : `${fmt.distance(zone.river_distance_m)}${zone.nearest_river ? ` · ${zone.nearest_river}` : ""}`}</span>
        </div>
      </div>

      <div className="ev-tabs" role="tablist">
        <button role="tab" aria-selected={tab === "preuves"} className={tab === "preuves" ? "on" : ""} onClick={() => setTab("preuves")}>Avant / après</button>
        <button role="tab" aria-selected={tab === "analyse"} className={tab === "analyse" ? "on" : ""} onClick={() => setTab("analyse")}>Analyse</button>
      </div>

      {tab === "preuves" ? (
        <div className="ev-body">
          <div className="section-title">
            Preuves satellite
            <div className="seg" role="group" aria-label="Rendu des vignettes">
              <button className={mode === "truecolor" ? "on" : ""} onClick={() => setMode("truecolor")}>Couleurs</button>
              <button className={mode === "ndvi" ? "on" : ""} onClick={() => setMode("ndvi")}>NDVI</button>
            </div>
          </div>
          <div className="compare">
            {(["before", "after"] as const).map((k) => {
              const side = cmp?.[k];
              return (
                <figure key={k}>
                  {side ? (
                    <img src={mode === "ndvi" ? side.ndvi_url : side.url}
                         alt={`Image ${k === "before" ? "avant" : "après"} détection`} loading="lazy" />
                  ) : <div className="empty">Indisponible</div>}
                  <figcaption><b>{k === "before" ? "Avant" : "Après"}</b><span>{side ? fmt.date(side.date) : "—"}</span></figcaption>
                  {side?.ndvi_mean != null && <figcaption className="sub"><span>NDVI moyen</span><span>{side.ndvi_mean.toFixed(2)}</span></figcaption>}
                </figure>
              );
            })}
          </div>
          <p className="caption">Contour rouge : zone détectée, au pixel près (10 m). Le NDVI passe du vert (végétation) au brun (sol nu).</p>
        </div>
      ) : (
        <div className="ev-body">
          <div className="metrics">
            <div className="metric"><div className="metric-label">ΔNDVI</div>
              <div className="metric-value" style={{ color: "var(--urgente)" }}>{zone.delta_ndvi.toFixed(2).replace(".", ",")}</div></div>
            <div className="metric"><div className="metric-label">khi²</div>
              <div className="metric-value">{zone.chi2 == null ? "—" : fmt.num(zone.chi2, 1)}</div></div>
            <div className="metric"><div className="metric-label">Surface</div>
              <div className="metric-value">{fmt.num(zone.surface_ha)}<span className="metric-unit">ha</span></div></div>
          </div>

          {zone.median_drop != null && zone.dispersion != null && (
            <div className="chi2-box">
              <div className="section-title">Détection par khi²</div>
              <p>
                Dans cette zone, la baisse de NDVI typique était de <b>{zone.median_drop.toFixed(2)}</b> (dispersion {zone.dispersion.toFixed(2)}).
                Ce pixel a baissé de <b>{zone.delta_ndvi.toFixed(2)}</b>, soit un khi² de <b>{fmt.num(zone.chi2, 1)}</b> —
                {zone.chi2 != null && zone.chi2 >= 6.63
                  ? " bien au-delà du seuil de 6,63 : la baisse est anormale, pas un simple effet saisonnier."
                  : " proche de la normale de la zone."}
              </p>
            </div>
          )}

          <div>
            <div className="section-title">Coordonnées GPS</div>
            <div className="gps">
              <span>{fmt.coord(zone.lat, zone.lon)}</span>
              <button className="icon-btn" onClick={copyGps} aria-label="Copier les coordonnées">{copied ? "✓" : "⧉"}</button>
            </div>
          </div>

          <div>
            <div className="section-title">Critères du score <span className="pts">{zone.score} pts</span></div>
            <div className="checklist">
              {zone.criteria.map((c) => (
                <div className="check" key={c.key}>
                  <span className={`check-icon ${c.met ? "ok" : "ko"}`}>{c.met ? "✓" : "✗"}</span>
                  <span className="check-label">{c.label}<br /><span className="check-value">{c.value}</span></span>
                  {c.weight > 0 && <span className="check-pts">{fmt.num(c.points, 0)}/{fmt.num(c.weight, 0)}</span>}
                </div>
              ))}
            </div>
          </div>

          {history.filter((p) => p.ndvi_mean !== null).length > 1 && (
            <div>
              <div className="section-title">Historique NDVI de la zone</div>
              <NdviSparkline points={history} />
            </div>
          )}

          <div className="ev-sources">
            Image de détection : {zone.image?.satellite === "landsat" ? "Landsat" : "Sentinel-2"} du {fmt.date(zone.image?.acquisition_date)}
            {zone.image?.cloud_cover != null && ` (${fmt.num(zone.image.cloud_cover, 0)} % nuages sur la zone)`}<br />
            Référence : {fmt.date(zone.reference_image?.acquisition_date)}
          </div>
        </div>
      )}
    </aside>
  );
}

function NdviSparkline({ points }: { points: NdviPoint[] }) {
  const pts = points.filter((p) => p.ndvi_mean !== null);
  const W = 340, H = 110, PL = 28, PR = 8, PT = 10, PB = 22;
  const x = (i: number) => PL + (i * (W - PL - PR)) / Math.max(1, pts.length - 1);
  const y = (v: number) => PT + ((1 - v) * (H - PT - PB)) / 1.2;
  const path = pts.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.ndvi_mean!)}`).join(" ");
  return (
    <svg width="100%" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Évolution du NDVI moyen de la zone">
      {[0, 0.4, 0.8].map((v) => (
        <g key={v}>
          <line x1={PL} x2={W - PR} y1={y(v)} y2={y(v)} stroke="#eee" />
          <text x={PL - 6} y={y(v) + 3} fontSize="9" fill="#767676" textAnchor="end">{v.toFixed(1)}</text>
        </g>
      ))}
      <path d={path} fill="none" stroke="#024ad8" strokeWidth="2" />
      {pts.map((p, i) => <circle key={i} cx={x(i)} cy={y(p.ndvi_mean!)} r={3.5} fill="#024ad8" stroke="#fff" strokeWidth="2" />)}
      <text x={PL} y={H - 6} fontSize="9" fill="#767676">{fmt.date(pts[0]?.date)}</text>
      <text x={W - PR} y={H - 6} fontSize="9" fill="#767676" textAnchor="end">{fmt.date(pts[pts.length - 1]?.date)}</text>
    </svg>
  );
}
