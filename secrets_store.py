#!/usr/bin/env python3
"""
secrets_store.py — claves de API guardadas localmente (no en config.json)
===========================================================================

Permite gestionar RAPIDAPI_KEY/OPENROUTER_API_KEY desde el dashboard (pestana
"Claves API") sin depender de exportarlas a mano en la terminal. Si existen
como variables de entorno (ej. en el cron), esas tienen prioridad sobre lo
guardado aqui — asi el cron sigue funcionando exactamente igual que antes.

secrets.json no se versiona (ver .gitignore) y se guarda con permisos 600.
"""

import json
import os
import stat
from pathlib import Path

SECRETS_FILE = Path(__file__).parent / "secrets.json"

_EMPTY = {"rapidapi_key": "", "openrouter_key": ""}


def load_secrets() -> dict:
    if SECRETS_FILE.exists():
        return {**_EMPTY, **json.loads(SECRETS_FILE.read_text(encoding="utf-8"))}
    return dict(_EMPTY)


def save_secrets(secrets: dict) -> None:
    tmp = SECRETS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(secrets, indent=2), encoding="utf-8")
    tmp.replace(SECRETS_FILE)
    os.chmod(SECRETS_FILE, stat.S_IRUSR | stat.S_IWUSR)


def get_api_keys() -> tuple[str | None, str | None]:
    """Resuelve las claves: lo guardado desde la GUI primero, variable de
    entorno como respaldo (para cron/instalaciones que nunca tocan la GUI)."""
    stored = load_secrets()
    rapidapi_key = stored.get("rapidapi_key") or os.environ.get("RAPIDAPI_KEY") or None
    openrouter_key = stored.get("openrouter_key") or os.environ.get("OPENROUTER_API_KEY") or None
    return rapidapi_key, openrouter_key
