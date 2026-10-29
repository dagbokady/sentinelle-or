import { useEffect, useState } from "react";
import MapView, { type MapFocus } from "./components/MapView";
import ZoneDetail from "./components/ZoneDetail";
import { api } from "./services/api";
import type { Health, Severity } from "./types";
import { SEVERITY_COLOR, SEVERITY_LABEL } from "./types";

export default function App() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [severity, setSeverity] = useState<Severity[]>([]);
  const [focus, setFocus] = useState<MapFocus | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setError(`Connexion à l'API impossible (${e.message})`));
    // Lien partageable : ?zone=<id> ouvre directement la fiche et centre la carte
    const id = new URLSearchParams(window.location.search).get("zone");
    if (id) {
      setSelectedId(id);
      api.zone(id).then((z) => setFocus({ bounds: z.bounds, key: Date.now() })).catch(() => {});
    }
  }, []);

  const toggle = (s: Severity) =>
    setSeverity((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-dot" />
          <div>
            <b>Sentinelle-Or</b>
            <span className="brand-sub">Surveillance satellite de l'orpaillage · Côte d'Ivoire</span>
          </div>
        </div>
        <div className="legend" role="group" aria-label="Filtrer par niveau">
          {(["urgente", "moderee", "surveillance"] as Severity[]).map((s) => {
            const on = severity.length === 0 || severity.includes(s);
            return (
              <button key={s} className={`chip ${on ? "on" : "off"}`} onClick={() => toggle(s)} aria-pressed={on}>
                <span className="sw" style={{ background: SEVERITY_COLOR[s] }} />
                {SEVERITY_LABEL[s]}
              </button>
            );
          })}
        </div>
        {health && (
          <div className="health" title="État du système">
            {health.detections} zone{health.detections > 1 ? "s" : ""} · {health.images} image{health.images > 1 ? "s" : ""}
          </div>
        )}
      </header>

      {error && <div className="error-box">{error}</div>}

      <div className="content">
        <MapView severity={severity} selectedId={selectedId} onSelect={setSelectedId} focus={focus} />
        {selectedId && (
          <ZoneDetail
            zoneId={selectedId}
            onClose={() => setSelectedId(null)}
            onShowOnMap={(bounds) => setFocus({ bounds, key: Date.now() })}
          />
        )}
      </div>
    </div>
  );
}
