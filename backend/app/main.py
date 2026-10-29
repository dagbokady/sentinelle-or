"""Point d'entrée FastAPI — Sentinelle-Or.

Sert les zones suspectes et les images avant/après à l'interface cartographique.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import zones
from app.config import get_settings
from app.utils.database import init_db

log = logging.getLogger("sentinelle.api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    init_db()
    yield


app = FastAPI(
    title="Sentinelle-Or API",
    description="Détection satellite de l'orpaillage clandestin en Côte d'Ivoire (Sentinel-2 / Landsat, ΔNDVI khi²).",
    version=__version__,
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins.split(","), allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

app.include_router(zones.router)
