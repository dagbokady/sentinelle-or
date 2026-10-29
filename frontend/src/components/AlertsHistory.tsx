import { useEffect, useMemo, useState } from "react";
import { api, fmt } from "../services/api";
import type { Severity, ZoneSummary } from "../types";
import { SEVERITY_LABEL } from "../types";

interface Props {
  severity: Severity[];
  onOpen: (zone: ZoneSummary) => void;
}

type SortKey = "detection_date" | "name" | "region" | "severity" | "score" | "surface_ha" | "delta_ndvi";
const SEV_RANK: Record<Severity, number> = { urgente: 3, moderee: 2, surveillance: 1 };

/** Onglet « Historique des alertes » : tableau triable/filtrable de toutes les détections. */
export default function AlertsHistory({ severity, onOpen }: Props) {
  const [rows, setRows] = useState<ZoneSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortKey>("detection_date");
  const [order, setOrder] = useState<"asc" | "desc">("desc");

  useEffect(() => {
    api.zoneList({ sort: "date", order: "desc", limit: 500 }).then(setRows).catch((e) => setError(String(e)));
  }, []);

  const toggleSort = (key: SortKey) => {
    if (key === sort) setOrder((o) => (o === "asc" ? "desc" : "asc"));
    else { setSort(key); setOrder(key === "name" || key === "region" ? "asc" : "desc"); }
  };

  const shown = useMemo(() => {
    if (!rows) return [];
    const q = query.trim().toLowerCase();
    let out = rows.filter((r) => {
      const sevOk = severity.length === 0 || severity.includes(r.severity);
      const qOk = !q || [r.name, r.commune, r.region].some((v) => v?.toLowerCase().includes(q));
      return sevOk && qOk;
    });
    const dir = order === "asc" ? 1 : -1;
    out = [...out].sort((a, b) => {
      let av: number | string, bv: number | string;
      if (sort === "severity") { av = SEV_RANK[a.severity]; bv = SEV_RANK[b.severity]; }
      else if (sort === "name") { av = (a.commune || a.name).toLowerCase(); bv = (b.commune || b.name).toLowerCase(); }
      else if (sort === "region") { av = (a.region || "").toLowerCase(); bv = (b.region || "").toLowerCase(); }
      else { av = a[sort] as number; bv = b[sort] as number; }
      return av < bv ? -dir : av > bv ? dir : 0;
    });
    return out;
  }, [rows, severity, query, sort, order]);

  if (error) return <div className="tab-view"><div className="error-box">{error}</div></div>;
  if (!rows) return <div className="tab-view"><div className="loading"><div className="spinner" /></div></div>;

  const arrow = (key: SortKey) => (sort === key ? (order === "asc" ? " ▲" : " ▼") : "");
  const col = (key: SortKey, label: string) => (
    <th className="sortable" onClick={() => toggleSort(key)} aria-sort={sort === key ? (order === "asc" ? "ascending" : "descending") : "none"}>
      {label}{arrow(key)}
    </th>
  );

  return (
    <div className="tab-view">
      <div className="view-head">
        <div>
          <h2 className="view-title">Historique des alertes</h2>
          <p className="view-sub">{shown.length} alerte{shown.length > 1 ? "s" : ""}{shown.length !== rows.length ? ` sur ${rows.length}` : ""} · la plus récente en tête</p>
        </div>
        <input className="search" type="search" placeholder="Rechercher commune, région…" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Rechercher une alerte" />
      </div>

      <div className="table-wrap">
        <table className="alerts-table">
          <thead>
            <tr>
              {col("detection_date", "Date")}
              {col("name", "Zone / commune")}
              {col("region", "Région")}
              {col("severity", "Niveau")}
              {col("score", "Score")}
              {col("surface_ha", "Surface")}
              {col("delta_ndvi", "ΔNDVI")}
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const code = r.name.split("· ").pop();
              return (
                <tr key={r.id} className="alert-row" onClick={() => onOpen(r)} tabIndex={0}
                    onKeyDown={(e) => { if (e.key === "Enter") onOpen(r); }} title="Ouvrir sur la carte">
                  <td className="nowrap">{fmt.date(r.detection_date)}</td>
                  <td>
                    <span className="al-commune">{r.commune || "Zone"}</span>
                    {r.is_recidive && <span className="recidive-tag" title="Récidive">récidive</span>}
                    <span className="al-code">{code}</span>
                  </td>
                  <td>{r.region || "—"}</td>
                  <td><span className={`sev-pill sev-${r.severity}`}>{SEVERITY_LABEL[r.severity]}</span></td>
                  <td><span className={`score-badge sm bg-${r.severity}`}>{r.score}</span></td>
                  <td className="nowrap">{fmt.num(r.surface_ha, 1)} ha</td>
                  <td className="nowrap" style={{ color: "var(--urgente)" }}>{r.delta_ndvi.toFixed(2).replace(".", ",")}</td>
                </tr>
              );
            })}
            {shown.length === 0 && (
              <tr><td colSpan={7} className="empty-row">Aucune alerte ne correspond aux filtres.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
