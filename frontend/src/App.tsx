import { useEffect, useState } from "react";
import MapView, { type MapFocus } from "./components/MapView";
import ZoneDetail from "./components/ZoneDetail";
import AlertsHistory from "./components/AlertsHistory";
import Dashboard from "./components/Dashboard";
import { api } from "./services/api";
import type { Health, Severity, ZoneSummary } from "./types";
import { SEVERITY_COLOR, SEVERITY_LABEL } from "./types";

type Tab = "carte" | "historique" | "stats";
const TABS: { id: Tab; label: string }[] = [
  { id: "carte", label: "Carte" },
  { id: "historique", label: "Historique des alertes" },
  { id: "stats", label: "Statistiques" },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("carte");
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

  // Ouvrir une alerte depuis l'historique : bascule sur la carte, sélectionne et centre la zone
  const openOnMap = (zone: ZoneSummary) => {
    setSelectedId(zone.id);
    setTab("carte");
    api.zone(zone.id).then((z) => setFocus({ bounds: z.bounds, key: Date.now() })).catch(() => {});
  };

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

        <nav className="tabs" role="tablist" aria-label="Sections">
          {TABS.map((t) => (
            <button key={t.id} role="tab" aria-selected={tab === t.id}
                    className={`tab ${tab === t.id ? "on" : ""}`} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </nav>

        {tab !== "stats" && (
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
        )}

        {health && (
          <div className="health" title="État du système">
            {health.detections} zone{health.detections > 1 ? "s" : ""} · {health.images} image{health.images > 1 ? "s" : ""}
          </div>
        )}
      </header>

      {error && <div className="error-box">{error}</div>}

      {tab === "carte" && (
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
      )}

      {tab === "historique" && <AlertsHistory severity={severity} onOpen={openOnMap} />}

      {tab === "stats" && <Dashboard onOpenHistory={() => setTab("historique")} />}
    </div>
  );
}
