"""Configuration centralisée : config.yml (paramètres métier) + variables d'environnement (secrets)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(PROJECT_DIR / ".env", BACKEND_DIR / ".env"), extra="ignore")

    database_url: str = f"sqlite:///{PROJECT_DIR / 'sentinelle.sqlite'}"
    config_path: str = str(BACKEND_DIR / "config.yml")
    data_dir: str = str(PROJECT_DIR / "data")

    # Sentinel-2 via Copernicus Data Space (optionnel : Earth Search fonctionne sans compte)
    copernicus_user: str = ""
    copernicus_password: str = ""
    # Landsat via USGS M2M (optionnel)
    usgs_user: str = ""
    usgs_token: str = ""

    cors_origins: str = "*"
    log_dir: str = str(PROJECT_DIR / "logs")


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_config() -> dict[str, Any]:
    """Paramètres métier (bbox, seuils khi², poids) chargés depuis config.yml."""
    with open(get_settings().config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_path(p: str) -> Path:
    """Chemins relatifs de config.yml → relatifs à la racine (ou DATA_DIR pour data/...)."""
    path = Path(p)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == "data":
        return Path(get_settings().data_dir).joinpath(*path.parts[1:])
    return PROJECT_DIR / path


def is_postgres() -> bool:
    return get_settings().database_url.startswith("postgresql")


os.makedirs(get_settings().log_dir, exist_ok=True)
