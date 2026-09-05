#!/usr/bin/env python3
"""
geo.py — inferencia de pais a partir del texto libre de "location"
====================================================================

La API JSearch no siempre informa el pais de una oferta (job_country llega
como null), pero el texto de ubicacion casi siempre trae la ciudad. Este
modulo mapea esa ciudad (o nombre de region/pais) a un codigo ISO 3166-1
alpha-2, para poder rellenar el hueco y mostrar la bandera correspondiente.

Es un diccionario estatico y manual, no un servicio de geocodificacion:
cubre las ciudades principales de los paises ya configurados en
search_countries (es, de, nl, uk/gb, fr) mas las que ya aparecen en
jobs.db. Anadir un pais de busqueda nuevo puede requerir ampliar
_CITY_COUNTRY a mano.
"""

import re
import unicodedata


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.strip().lower()


def _strip_location_noise(location: str) -> str:
    """'Riederich   *  ueber NEURA Robotics' -> 'Riederich'
    'Hannover (+12 weitere Standorte)  *  ueber X' -> 'Hannover'
    'Santiago de Compostela, Municipality of ...' -> 'Santiago de Compostela'"""
    head = location.split("•", 1)[0]
    head = re.sub(r"\(\+?\s*\d+.*?\)", "", head)
    head = head.split(",", 1)[0]
    return head.strip()


_CITY_COUNTRY: dict[str, str] = {
    # Espana
    "madrid": "ES", "comunidad de madrid": "ES", "barcelona": "ES", "cordoba": "ES",
    "valencia": "ES", "sevilla": "ES", "bilbao": "ES", "zaragoza": "ES", "malaga": "ES",
    "alcobendas": "ES", "torrejon de ardoz": "ES", "santiago de compostela": "ES",
    "espana": "ES", "spain": "ES",
    # Alemania
    "berlin": "DE", "hamburg": "DE", "munchen": "DE", "munich": "DE", "koln": "DE",
    "cologne": "DE", "frankfurt": "DE", "stuttgart": "DE", "dusseldorf": "DE",
    "dortmund": "DE", "essen": "DE", "bremen": "DE", "hannover": "DE", "nurnberg": "DE",
    "bochum": "DE", "bonn": "DE", "mannheim": "DE", "karlsruhe": "DE", "wiesbaden": "DE",
    "munster": "DE", "augsburg": "DE", "freiburg": "DE", "freiburg im breisgau": "DE",
    "tubingen": "DE", "nurtingen": "DE", "riederich": "DE", "giebelstadt": "DE",
    "paderborn": "DE", "haiger": "DE", "offenburg": "DE", "muhltal": "DE",
    "deutschland": "DE", "germany": "DE",
    # Paises Bajos
    "amsterdam": "NL", "rotterdam": "NL", "den haag": "NL", "the hague": "NL",
    "utrecht": "NL", "eindhoven": "NL", "tilburg": "NL", "groningen": "NL",
    "delft": "NL", "zwolle": "NL", "gorinchem": "NL", "maassluis": "NL",
    "rijssen": "NL", "bunnik": "NL", "zuid-holland": "NL", "noord-holland": "NL",
    "nederland": "NL", "netherlands": "NL", "holland": "NL",
    # Francia
    "paris": "FR", "marseille": "FR", "lyon": "FR", "toulouse": "FR",
    "nantes": "FR", "strasbourg": "FR", "bordeaux": "FR", "lille": "FR",
    "villeneuve-d'ascq": "FR", "france": "FR",
    # Reino Unido (ISO real es GB, no UK)
    "london": "GB", "manchester": "GB", "birmingham": "GB", "leeds": "GB",
    "glasgow": "GB", "edinburgh": "GB", "bristol": "GB",
    "united kingdom": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
}


def infer_country(location: str | None) -> str | None:
    if not location:
        return None
    normalized = _normalize(_strip_location_noise(location))
    if not normalized:
        return None
    if normalized in _CITY_COUNTRY:
        return _CITY_COUNTRY[normalized]
    for city, code in _CITY_COUNTRY.items():
        if city in normalized:
            return code
    return None
