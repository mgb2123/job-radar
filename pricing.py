#!/usr/bin/env python3
"""
pricing.py — precios por token de los modelos de OpenRouter, cacheados
=========================================================================

Consulta https://openrouter.ai/api/v1/models (publico, sin autenticacion)
para obtener el precio por token de cada modelo, y lo cachea en disco 24h
para no golpear la red en cada carga de la pestana "Coste". Si la red falla
o el modelo no aparece listado, se devuelve precio desconocido en vez de
romper la pestana.
"""

import json
import time
from pathlib import Path

import requests

CACHE_FILE = Path(__file__).parent / "pricing_cache.json"
CACHE_TTL_SECONDS = 24 * 60 * 60
MODELS_URL = "https://openrouter.ai/api/v1/models"


def _fetch_models() -> dict:
    resp = requests.get(MODELS_URL, timeout=10)
    resp.raise_for_status()
    return {
        m["id"]: {
            "prompt": float(m.get("pricing", {}).get("prompt", 0) or 0),
            "completion": float(m.get("pricing", {}).get("completion", 0) or 0),
        }
        for m in resp.json().get("data", [])
        if m.get("id")
    }


def get_model_prices() -> dict:
    """Precio por token (no por millon) de cada modelo, cache-first."""
    if CACHE_FILE.exists():
        try:
            cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if time.time() - cache.get("fetched_at", 0) <= CACHE_TTL_SECONDS:
                return cache["models"]
        except (json.JSONDecodeError, OSError, KeyError):
            pass
    try:
        models = _fetch_models()
    except requests.RequestException:
        return {}
    tmp = CACHE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"fetched_at": time.time(), "models": models}), encoding="utf-8")
    tmp.replace(CACHE_FILE)
    return models


def price_per_million(model: str) -> tuple[float | None, float | None]:
    """(precio_input_por_millon, precio_output_por_millon) o (None, None) si se desconoce."""
    prices = get_model_prices().get(model)
    if not prices:
        return None, None
    return prices["prompt"] * 1_000_000, prices["completion"] * 1_000_000
