"""Score de risque composite (0-100) et niveau d'alerte.

Chaque critère produit une note partielle dans [0, 1], pondérée par config.yml, puis
agrégée en un score sur 100 donnant trois niveaux : surveillance, modérée, urgente.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import get_config

CRITERIA_LABELS = {
    "delta_ndvi": "Perte de végétation brutale (ΔNDVI)",
    "irregularity": "Forme irrégulière",
    "river_proximity": "Proximité d'un cours d'eau",
    "recidive": "Récidive sur la zone (12 mois)",
}


def score_delta_ndvi(delta: float, max_abs: float = 0.7) -> float:
    """min(1, |ΔNDVI| / 0.7) — uniquement pour une perte (ΔNDVI négatif)."""
    return 0.0 if delta >= 0 else min(1.0, abs(delta) / max_abs)


def score_irregularity(compacity: float, regular: float = 0.75, irregular: float = 0.25) -> float:
    """Polsby-Popper bas = forme irrégulière = score élevé (interpolation linéaire)."""
    if compacity <= irregular:
        return 1.0
    if compacity >= regular:
        return 0.0
    return (regular - compacity) / (regular - irregular)


def score_river_proximity(distance_m: float | None, full_m: float = 500, zero_m: float = 2000) -> float:
    """< 500 m → 1 ; > 2 km → 0 ; décroissance linéaire entre les deux."""
    if distance_m is None:
        return 0.0
    if distance_m <= full_m:
        return 1.0
    if distance_m >= zero_m:
        return 0.0
    return (zero_m - distance_m) / (zero_m - full_m)


def severity_for(score: int, cfg: dict | None = None) -> str:
    t = (cfg or get_config())["scoring"]["thresholds"]
    if score >= t["urgente"]:
        return "urgente"
    if score >= t["moderee"]:
        return "moderee"
    return "surveillance"


@dataclass
class ScoreResult:
    score: int
    severity: str
    details: dict = field(default_factory=dict)


def compute_score(delta_ndvi: float, compacity: float, river_distance_m: float | None, recidive: bool,
                  cfg: dict | None = None) -> ScoreResult:
    """Agrège les critères en un score 0-100 et un niveau d'alerte."""
    cfg = cfg or get_config()
    s = cfg["scoring"]
    w = s["weights"]
    partials = {
        "delta_ndvi": score_delta_ndvi(delta_ndvi, s["delta_ndvi_max"]),
        "irregularity": score_irregularity(compacity, s["compacity_regular"], s["compacity_irregular"]),
        "river_proximity": score_river_proximity(river_distance_m, s["river_distance_full_m"], s["river_distance_zero_m"]),
        "recidive": 1.0 if recidive else 0.0,
    }
    active = {k: w[k] for k in partials if w.get(k, 0) > 0}
    total_w = sum(active.values()) or 1
    details = {}
    raw = 0.0
    for k, weight in active.items():
        effective = 100 * weight / total_w
        pts = partials[k] * effective
        raw += pts
        details[k] = {
            "label": CRITERIA_LABELS[k],
            "partial": round(partials[k], 3),
            "weight": round(effective, 1),
            "points": round(pts, 1),
            "met": partials[k] >= 0.5,
        }
    score = int(round(min(100.0, max(0.0, raw))))
    return ScoreResult(score=score, severity=severity_for(score, cfg), details=details)
