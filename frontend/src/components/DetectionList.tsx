import { useMemo, useState } from "react";
import { fmt } from "../services/api";
import type { Severity, ZoneSummary } from "../types";
import { SEVERITY_COLOR, SEVERITY_LABEL } from "../types";

interface Props {
  zones: ZoneSummary[] | null;
  error: string | null;
  severity: Severity[];
  selectedId: string | null;
  collapsed: boolean;
  onToggle: () => void;
  onSelect: (zone: ZoneSummary) => void;
}

type SortKey = "score" | "niveau";
const SEV_RANK: Record<Severity, number> = { urgente: 3, moderee: 2, surveillance: 1 };

/** Référence courte lisible depuis le nom de zone (« … · CODE »). */
function zoneRef(z: ZoneSummary): string {
  return z.name.split("· ").pop() || z.name;
}

/** Panneau latéral listant les zones détectées : tri par score / niveau, clic → fiche. */
export default function DetectionList({ zones, error, severity, selectedId, collapsed, onToggle, onSelect }: Props) {
  const [sort, setSort] = useState<SortKey>("score");

  const shown = useMemo(() => {
    if (!zones) return [];
    const list = zones.filter((z) => severity.length === 0 || severity.includes(z.severity));
    return [...list].sort((a, b) =>
      sort === "score"
        ? b.score - a.score
        : SEV_RANK[b.severity] - SEV_RANK[a.severity] || b.score - a.score,
    );
  }, [zones, severity, sort]);

  const total = zones?.length ?? 0;

  if (collapsed) {
    return (
      <button className="det-reopen" onClick={onToggle} aria-label="Afficher la liste des détections">
        <span className="det-reopen-ico" aria-hidden>☰</span>
        <span>{zones ? `${total} zones` : "Détections"}</span>
      </button>
    );
  }

  return (
    <aside className="det-panel" aria-label="Zones détectées">
      <div className="det-head">
        <div className="det-title">
          Zones détectées
          {zones && <span className="det-count">{shown.length}</span>}
        </div>
        <button className="det-collapse" onClick={onToggle} aria-label="Replier la liste" title="Replier">
          ‹
        </button>
      </div>

      <div className="det-sort" role="group" aria-label="Trier">
        <span className="det-sort-lbl">Trier</span>
        <div className="seg">
          <button className={sort === "score" ? "on" : ""} onClick={() => setSort("score")} aria-pressed={sort === "score"}>
            Score
          </button>
          <button className={sort === "niveau" ? "on" : ""} onClick={() => setSort("niveau")} aria-pressed={sort === "niveau"}>
            Niveau
          </button>
        </div>
      </div>

      <div className="det-list">
        {error ? (
          <div className="det-state">Chargement impossible.<br />{error}</div>
        ) : !zones ? (
          Array.from({ length: 7 }).map((_, i) => (
            <div className="det-item sk" key={i} aria-hidden>
              <span className="sk-dot" />
              <span className="det-main">
                <span className="sk-line w60" />
                <span className="sk-line w40" />
              </span>
              <span className="sk-badge" />
            </div>
          ))
        ) : shown.length === 0 ? (
          <div className="det-state">Aucune zone ne correspond aux filtres.</div>
        ) : (
          shown.map((z) => (
            <button
              key={z.id}
              className={`det-item ${z.id === selectedId ? "on" : ""}`}
              onClick={() => onSelect(z)}
              title={`Centrer sur ${z.commune || zoneRef(z)}`}
            >
              <span className="det-dot" style={{ background: SEVERITY_COLOR[z.severity] }} aria-hidden />
              <span className="det-main">
                <span className="det-ref">
                  {z.commune || "Zone"}
                  {z.is_recidive && <span className="recidive-tag">récidive</span>}
                </span>
                <span className="det-meta">
                  <span className={`sev-pill sev-${z.severity}`}>{SEVERITY_LABEL[z.severity]}</span>
                  {z.region ? ` ${z.region} · ` : " "}
                  {fmt.num(z.surface_ha, 1)} ha
                </span>
              </span>
              <span className={`score-badge sm bg-${z.severity}`} title="Score sur 100">{z.score}</span>
            </button>
          ))
        )}
      </div>
    </aside>
  );
}
