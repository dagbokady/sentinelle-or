"""Backfill de démonstration : Sentinel-2 (Earth Search, sans compte) sur quelques zones.

Fenêtre et nombre d'images volontairement limités pour une démo rapide.
"""
import logging
from datetime import datetime, timedelta, timezone

from app.config import get_config
from app.processing.acquisition import EarthSearchProvider, run_acquisition
from app.processing.pipeline import process_pending
from app.utils.database import init_db, session_scope

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

DEMO_AOIS = {"HIRE", "KOKUMBO", "DABAKALA"}
DAYS = 260
LIMIT = 8  # images max par zone

init_db()
aois = [a for a in get_config().get("aois", []) if a["id"] in DEMO_AOIS]
now = datetime.now(timezone.utc).replace(tzinfo=None)
start = now - timedelta(days=DAYS)

with session_scope() as db:
    imgs = run_acquisition(db, [EarthSearchProvider(aois)], now=now, start=start, limit=LIMIT, commit_each=True)
print(f"ACQUISITION: {len(imgs)} image(s)")

with session_scope() as db:
    reports = process_pending(db)
print(f"TRAITEMENT: {len(reports)} image(s), {sum(r.detections for r in reports)} détection(s)")
