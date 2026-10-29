import { useEffect, useMemo, useState } from "react";
import { api, fmt } from "../services/api";
import type { Severity, ZoneSummary } from "../types";
import { SEVERITY_COLOR, SEVERITY_LABEL } from "../types";

interface Props {
  onOpenHistory: () => void;
}

const SEVERITIES: Severity[] = ["urgente", "moderee", "surveillance"];

/** Onglet « Statistiques » : synthèse des alertes (sévérité, région, surface, évolution). */
export default function Dashboard({ onOpenHistory }: Props) {
  const [rows, setRows] = useState<ZoneSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.zoneList({ sort: "date", order: "desc", limit: 500 }).then(setRows).catch((e) => setError(String(e)));
  }, []);

  const stats = useMemo(() => {
    if (!rows) return null;
    const bySev = Object.fromEntries(SEVERITIES.map((s) => [s, 0])) as Record<Severity, number>;
    const byRegion = new Map<string, number>();
    const byMonth = new Map<string, number>();
    let surface = 0, recidives = 0;
    for (const r of rows) {
      bySev[r.severity] = (bySev[r.severity] ?? 0) + 1;
      const reg = r.region || "Non précisée";
      byRegion.set(reg, (byRegion.get(reg) ?? 0) + 1);
      const d = new Date(r.detection_date);
      const mk = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
      byMonth.set(mk, (byMonth.get(mk) ?? 0) + 1);
      surface += r.surface_ha || 0;
      if (r.is_recidive) recidives += 1;
    }
    const regions = [...byRegion.entries()].sort((a, b) => b[1] - a[1]);
    const months = [...byMonth.entries()].sort((a, b) => a[0].localeCompare(b[0]));
    return { total: rows.length, bySev, regions, months, surface, recidives };
  }, [rows]);

  if (error) return <div className="tab-view"><div className="error-box">{error}</div></div>;
  if (!stats) return <div className="tab-view"><div className="loading"><div className="spinner" /></div></div>;

  const maxMonth = Math.max(1, ...stats.months.map((m) => m[1]));
  const maxRegion = Math.max(1, ...stats.regions.map((r) => r[1]));

  return (
    <div className="tab-view">
      <div className="view-head">
        <div>
          <h2 className="view-title">Statistiques</h2>
          <p className="view-sub">Synthèse de toutes les alertes détectées</p>
        </div>
        <button className="pill-btn link" onClick={onOpenHistory}>Voir l'historique →</button>
      </div>

      <div className="stat-grid">
        <div className="stat-card">
          <div className="stat-num">{stats.total}</div>
          <div className="stat-lbl">Alertes au total</div>
        </div>
        <div className="stat-card">
          <div className="stat-num" style={{ color: "var(--urgente)" }}>{stats.bySev.urgente}</div>
          <div className="stat-lbl">Urgentes</div>
        </div>
        <div className="stat-card">
          <div className="stat-num">{fmt.num(stats.surface, 0)}<span className="stat-unit"> ha</span></div>
          <div className="stat-lbl">Surface touchée</div>
        </div>
        <div className="stat-card">
          <div className="stat-num">{stats.recidives}</div>
          <div className="stat-lbl">Récidives</div>
        </div>
      </div>

      <div className="stat-cols">
        <section className="stat-block">
          <div className="section-title">Répartition par niveau</div>
          <div className="sev-bars">
            {SEVERITIES.map((s) => {
              const n = stats.bySev[s];
              const pct = stats.total ? (n / stats.total) * 100 : 0;
              return (
                <div className="sev-bar-row" key={s}>
                  <span className="sev-bar-lbl"><span className="sw" style={{ background: SEVERITY_COLOR[s] }} />{SEVERITY_LABEL[s]}</span>
                  <div className="sev-bar-track"><div className="sev-bar-fill" style={{ width: `${pct}%`, background: SEVERITY_COLOR[s] }} /></div>
                  <span className="sev-bar-n">{n}</span>
                </div>
              );
            })}
          </div>
        </section>

        <section className="stat-block">
          <div className="section-title">Alertes par région</div>
          <div className="sev-bars">
            {stats.regions.map(([reg, n]) => (
              <div className="sev-bar-row" key={reg}>
                <span className="sev-bar-lbl" title={reg}>{reg}</span>
                <div className="sev-bar-track"><div className="sev-bar-fill" style={{ width: `${(n / maxRegion) * 100}%`, background: "var(--accent)" }} /></div>
                <span className="sev-bar-n">{n}</span>
              </div>
            ))}
            {stats.regions.length === 0 && <p className="caption">Aucune donnée.</p>}
          </div>
        </section>
      </div>

      <section className="stat-block">
        <div className="section-title">Évolution dans le temps</div>
        {stats.months.length > 0 ? (
          <div className="timeline">
            {stats.months.map(([mk, n]) => (
              <div className="tl-col" key={mk} title={`${n} alerte${n > 1 ? "s" : ""}`}>
                <div className="tl-bar-wrap"><div className="tl-bar" style={{ height: `${(n / maxMonth) * 100}%` }}><span className="tl-n">{n}</span></div></div>
                <span className="tl-lbl">{monthLabel(mk)}</span>
              </div>
            ))}
          </div>
        ) : <p className="caption">Aucune donnée.</p>}
      </section>
    </div>
  );
}

function monthLabel(mk: string): string {
  const [y, m] = mk.split("-");
  const names = ["jan", "fév", "mar", "avr", "mai", "jun", "jul", "aoû", "sep", "oct", "nov", "déc"];
  return `${names[Number(m) - 1]} ${y.slice(2)}`;
}
