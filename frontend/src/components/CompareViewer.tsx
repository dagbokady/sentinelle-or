import { useEffect, useRef, useState } from "react";
import { frameSrc, fmt } from "../services/api";
import type { ZoneFrame } from "../types";

interface Props {
  frames: ZoneFrame[];
  initialMode?: "truecolor" | "ndvi";
  onClose: () => void;
}

type Layout = "side" | "swipe" | "chrono";
const EXTENTS: [string, number][] = [["Proche", 500], ["Normal", 1000], ["Large", 2500]];

/** Visionneuse agrandie des preuves satellite : comparer deux dates (côte à côte ou curseur),
 *  ou faire défiler toutes les dates en live. Peut passer en plein écran (grand écran). */
export default function CompareViewer({ frames, initialMode = "truecolor", onClose }: Props) {
  const refIdx = Math.max(0, frames.findIndex((f) => f.is_reference));
  const detIdx = Math.max(0, frames.findIndex((f) => f.is_detection));
  const [mode, setMode] = useState<"truecolor" | "ndvi">(initialMode);
  const [layout, setLayout] = useState<Layout>("side");
  const [extent, setExtent] = useState(1000);
  const [leftIdx, setLeftIdx] = useState(refIdx);
  const [rightIdx, setRightIdx] = useState(detIdx);
  const [scrub, setScrub] = useState(detIdx);
  const [pos, setPos] = useState(50);
  const [playing, setPlaying] = useState(false);
  const [fs, setFs] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  const src = (f: ZoneFrame, size = 1024) => frameSrc(mode === "ndvi" ? f.ndvi_url : f.url, { mode, size, extent });

  // Échap pour fermer, et suivi de l'état plein écran
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !document.fullscreenElement) onClose(); };
    const onFs = () => setFs(!!document.fullscreenElement);
    window.addEventListener("keydown", onKey);
    document.addEventListener("fullscreenchange", onFs);
    return () => { window.removeEventListener("keydown", onKey); document.removeEventListener("fullscreenchange", onFs); };
  }, [onClose]);

  // Lecture automatique de la chronologie
  useEffect(() => {
    if (!playing || layout !== "chrono") return;
    const t = setInterval(() => setScrub((i) => (i + 1) % frames.length), 1100);
    return () => clearInterval(t);
  }, [playing, layout, frames.length]);

  // Précharge les images pour un défilement fluide
  useEffect(() => { frames.forEach((f) => { const im = new Image(); im.src = src(f, 768); }); }, [mode, extent]); // eslint-disable-line

  const toggleFullscreen = () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else root.current?.requestFullscreen?.();
  };

  const seg = <T extends string | number>(val: T, set: (v: T) => void, opts: readonly (readonly [string, T])[]) => (
    <div className="seg">{opts.map(([l, v]) => <button key={String(v)} className={val === v ? "on" : ""} onClick={() => set(v)}>{l}</button>)}</div>
  );

  const DatePicker = ({ idx, set }: { idx: number; set: (i: number) => void }) => (
    <select className="date-select" value={idx} onChange={(e) => set(Number(e.target.value))}>
      {frames.map((f, i) => (
        <option key={f.date} value={i}>
          {fmt.date(f.date)}{f.is_reference ? " · réf." : f.is_detection ? " · détection" : ""}
          {f.cloud_cover != null ? ` · ${Math.round(f.cloud_cover)} % nuages` : ""}
        </option>
      ))}
    </select>
  );

  const cur = frames[scrub];
  const tag = (f: ZoneFrame) => f.is_reference ? "Référence" : f.is_detection ? "Détection" : null;

  return (
    <div className="cv-backdrop" ref={root} role="dialog" aria-modal="true" aria-label="Preuves satellite agrandies">
      <div className="cv-head">
        <b>Preuves satellite — {frames.length} dates</b>
        <div className="cv-tools">
          {seg(layout, setLayout, [["Côte à côte", "side"], ["Curseur", "swipe"], ["Chronologie", "chrono"]] as const)}
          {seg(mode, setMode, [["Couleurs", "truecolor"], ["NDVI", "ndvi"]] as const)}
          {seg(extent, setExtent, EXTENTS)}
          <button className="cv-btn" onClick={toggleFullscreen}>{fs ? "⤡ Quitter" : "⤢ Plein écran"}</button>
          <button className="cv-btn" onClick={onClose} aria-label="Fermer">✕</button>
        </div>
      </div>

      <div className="cv-body">
        {layout === "side" && (
          <div className="cv-side">
            {[{ idx: leftIdx, set: setLeftIdx }, { idx: rightIdx, set: setRightIdx }].map((p, k) => {
              const f = frames[p.idx];
              return (
                <figure key={k} className="cv-pane">
                  <div className="cv-img-wrap">
                    <img src={src(f)} alt={`Zone au ${f.date}`} />
                    {tag(f) && <span className={`cv-badge ${f.is_detection ? "det" : "ref"}`}>{tag(f)}</span>}
                  </div>
                  <figcaption>
                    <DatePickerLike idx={p.idx} set={p.set} frames={frames} />
                    {f.cloud_cover != null && <span className="cv-cloud">{Math.round(f.cloud_cover)} % nuages</span>}
                  </figcaption>
                </figure>
              );
            })}
          </div>
        )}

        {layout === "swipe" && (
          <div className="cv-swipe">
            <div className="cv-swipe-box">
              <img src={src(frames[rightIdx])} alt="Après" />
              <img src={src(frames[leftIdx])} alt="Avant" style={{ clipPath: `inset(0 ${100 - pos}% 0 0)` }} />
              <div className="cv-swipe-line" style={{ left: `${pos}%` }} />
              <span className="cv-swipe-tag left">{fmt.date(frames[leftIdx].date)}</span>
              <span className="cv-swipe-tag right">{fmt.date(frames[rightIdx].date)}</span>
            </div>
            <input type="range" min={0} max={100} value={pos} onChange={(e) => setPos(Number(e.target.value))} aria-label="Curseur de comparaison" />
            <div className="cv-swipe-dates">
              <label>Avant <DatePicker idx={leftIdx} set={setLeftIdx} /></label>
              <label>Après <DatePicker idx={rightIdx} set={setRightIdx} /></label>
            </div>
          </div>
        )}

        {layout === "chrono" && (
          <div className="cv-chrono">
            <div className="cv-img-wrap big">
              <img src={src(cur, 1280)} alt={`Zone au ${cur.date}`} />
              {tag(cur) && <span className={`cv-badge ${cur.is_detection ? "det" : "ref"}`}>{tag(cur)}</span>}
            </div>
            <div className="cv-timeline">
              <button className="cv-btn" onClick={() => setPlaying((p) => !p)}>{playing ? "⏸" : "▶"}</button>
              <input type="range" min={0} max={frames.length - 1} value={scrub} onChange={(e) => { setPlaying(false); setScrub(Number(e.target.value)); }} aria-label="Frise temporelle" />
              <div className="cv-cur">
                <b>{fmt.date(cur.date)}</b>
                {cur.cloud_cover != null && <> · {Math.round(cur.cloud_cover)} % nuages</>}
                {tag(cur) && <> · {tag(cur)}</>}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function DatePickerLike({ idx, set, frames }: { idx: number; set: (i: number) => void; frames: ZoneFrame[] }) {
  return (
    <select className="date-select" value={idx} onChange={(e) => set(Number(e.target.value))}>
      {frames.map((f, i) => (
        <option key={f.date} value={i}>
          {fmt.date(f.date)}{f.is_reference ? " · réf." : f.is_detection ? " · détection" : ""}
        </option>
      ))}
    </select>
  );
}
