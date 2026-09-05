#!/usr/bin/env python3
"""
config.py — configuracion compartida entre job_radar.py y dashboard.py
=======================================================================

Los valores viven en config.json (generado/editado desde el dashboard,
pestana "Configuracion"). Si config.json no existe todavia (instalacion
nueva, o antes de usar el dashboard), se usan los valores de DEFAULT_CONFIG
y job_radar.py funciona exactamente igual que si la config estuviera
hardcodeada como antes.

job_radar.py SOLO LEE config.json (nunca escribe) para evitar carreras con
el dashboard, que es el unico que llama a save_config().
"""

import json
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "config.json"

NIVELES_MCER = ["A1", "A2", "B1", "B2", "C1", "C2"]


def nivel_index(nivel: str | None) -> int | None:
    """Indice 0-5 de un nivel MCER (A1..C2), o None si no es valido/esta vacio."""
    if not nivel:
        return None
    try:
        return NIVELES_MCER.index(nivel.strip().upper())
    except ValueError:
        return None


DEFAULT_CONFIG = {
    "search_queries": [
        "robotics",
        "automation engineer",
        "computer vision",
        "ROS2",
    ],
    "search_countries": ["es", "de", "nl", "uk", "fr"],
    "results_per_query": 15,
    "openrouter_model": "google/gemini-2.5-flash-lite",
    "score_threshold": 65,
    # Tu nivel MCER (A1-C2) en cada idioma, para comparar con lo que pida
    # cada oferta. Vacio = no indicado (no se compara, solo se informa).
    "idiomas_usuario": {
        "ingles": "",
        "frances": "",
        "aleman": "",
        "italiano": "",
        "portugues": "",
    },
    "idiomas_usuario_otros": [],
    # Placeholder generico — tu perfil real se edita desde el dashboard
    # (pestana "Configuracion") y se guarda en config.json, que no se
    # versiona (ver .gitignore). Cuanto mas especifico sea tu CV_CONTEXT
    # real, mejor filtra el modelo.
    "cv_context": """
Sustituye este texto por un resumen de tu perfil/CV y tus preferencias
(nivel de experiencia, areas de interes, ubicacion, idiomas, que evitar).
Cuanto mas especifico, mejor filtra el modelo las ofertas que encajan.
""",
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return {**DEFAULT_CONFIG, **data}
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict) -> None:
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_FILE)
