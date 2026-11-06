import { useEffect, useMemo, useState } from "react";
import MapView, { type MapFocus } from "./components/MapView";
import ZoneDetail from "./components/ZoneDetail";
import AlertsHistory from "./components/AlertsHistory";
import Dashboard from "./components/Dashboard";
import DetectionList from "./components/DetectionList";
import { api, fmt } from "./services/api";
import type { Health, Severity, ZoneSummary } from "./types";
import { SEVERITY_COLOR, SEVERITY_LABEL } from "./types";

type Tab = "carte" | "historique" | "stats";
const TABS: { id: Tab; label: string }[] = [
  { id: "carte", label: "Carte" },
  { id: "historique", label: "Historique des alertes" },
  { id: "stats", label: "Statistiques" },
];
const SEVERITIES: Severity[] = ["urgente", "moderee", "surveillance"];
const ABOUT_KEY = "sentinelle.about.hidden";

const isMobile = () => typeof window !== "undefined" && window.matchMedia("(max-width: 760px)").matches;

export default function App() {
  const initialZoneId = new URLSearchParams(window.location.search).get("zone");

  const [tab, setTab] = useState<Tab>("carte");
  const [selectedId, setSelectedId] = useState<string | null>(initialZoneId);
  const [severity, setSeverity] = useState<Severity[]>([]);
  const [focus, setFocus] = useState<MapFocus | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [zones, setZones] = useState<ZoneSummary[] | null>(null);
  const [zonesError, setZonesError] = useState<string | null>(null);
  const [listCollapsed, setListCollapsed] = useState<boolean>(isMobile);
  const [aboutOpen, setAboutOpen] = useState<boolean>(() => {
    try { return localStorage.getItem(ABOUT_KEY) !== "1"; } catch { return true; }
  });

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setError(`Connexion à l'API impossible (${e.message})`));
    api
      .zoneList({ sort: "score", order: "desc", limit: 500 })
      .then(setZones)
      .catch((e) => setZonesError(String(e.message ?? e)));
    // Lien partageable : ?zone=<id> ouvre directement la fiche et centre la carte
    if (initialZoneId) {
      api.zone(initialZoneId).then((z) => setFocus({ bounds: z.bounds, key: Date.now() })).catch(() => {});
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const synth = useMemo(() => {
    if (!zones) return null;
    const bySev = { urgente: 0, moderee: 0, surveillance: 0 } as Record<Severity, number>;
    let last = "";
    for (const z of zones) {
      bySev[z.severity] += 1;
      if (z.detection_date > last) last = z.detection_date;
    }
    return { total: zones.length, bySev, last };
  }, [zones]);

  const toggle = (s: Severity) =>
    setSeverity((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));

  // Ouvrir une zone : bascule sur la carte, sélectionne et recentre
  const openOnMap = (zone: ZoneSummary) => {
    setSelectedId(zone.id);
    setTab("carte");
    api.zone(zone.id).then((z) => setFocus({ bounds: z.bounds, key: Date.now() })).catch(() => {});
  };

  // Clic depuis le panneau liste : recentre et, sur mobile, replie la liste pour voir la fiche
  const selectFromList = (zone: ZoneSummary) => {
    openOnMap(zone);
    if (isMobile()) setListCollapsed(true);
  };

  const dismissAbout = () => {
    setAboutOpen(false);
    try { localStorage.setItem(ABOUT_KEY, "1"); } catch { /* ignore */ }
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
            {SEVERITIES.map((s) => {
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

        <div className="synth" title={health ? `${health.images} image${health.images > 1 ? "s" : ""} analysée${health.images > 1 ? "s" : ""}` : undefined}>
          {synth ? (
            <>
              <div className="synth-total"><b>{synth.total}</b> zones détectées</div>
              <div className="synth-sev" role="group" aria-label="Répartition par niveau">
                {SEVERITIES.map((s) => (
                  <span key={s} className="synth-chip" title={SEVERITY_LABEL[s]}>
                    <span className="sw" style={{ background: SEVERITY_COLOR[s] }} />
                    {synth.bySev[s]}
                  </span>
                ))}
              </div>
              {synth.last && <div className="synth-date">Dernière analyse · {fmt.date(synth.last)}</div>}
            </>
          ) : (
            <div className="synth-sk"><span className="sk-line w120" /><span className="sk-line w80" /></div>
          )}
        </div>
      </header>

      {error && <div className="error-box">{error}</div>}

      {tab === "carte" && (
        <div className="content">
          <DetectionList
            zones={zones}
            error={zonesError}
            severity={severity}
            selectedId={selectedId}
            collapsed={listCollapsed}
            onToggle={() => setListCollapsed((c) => !c)}
            onSelect={selectFromList}
          />
          <MapView
            severity={severity}
            selectedId={selectedId}
            onSelect={setSelectedId}
            focus={focus}
            autoFit={!initialZoneId}
            about={aboutOpen ? <AboutCard onClose={dismissAbout} /> : null}
          />
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

function AboutCard({ onClose }: { onClose: () => void }) {
  return (
    <div className="about-card" role="note" aria-label="À propos de la carte">
      <button className="about-x" onClick={onClose} aria-label="Fermer et ne plus afficher">×</button>
      <b>Comment lire cette carte</b>
      <p>
        Sentinelle-Or compare des images satellites avant / après pour repérer la végétation
        détruite par l'orpaillage clandestin. Chaque zone colorée est une détection, notée sur 100.
      </p>
      <div className="about-legend">
        {SEVERITIES.map((s) => (
          <span key={s}><span className="sw" style={{ background: SEVERITY_COLOR[s] }} />{SEVERITY_LABEL[s]}</span>
        ))}
      </div>
    </div>
  );
}
