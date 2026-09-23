"""Altitude, via l'API REST d'altimetrie de l'IGN.

EXCEPTION VOLONTAIRE au reste du plugin : toutes les autres covariables sont
pilotees par data/couches_selection.json et interrogees en WFS GetFeature
(voir covariates.py / collector.py). L'altitude ne rentre pas dans ce moule
— ce n'est pas une couche WFS mais un point de calcul REST distinct, sans
notion de geometrie en retour ni de champs_cibles — donc elle est cablee en
dur ici plutot que d'etre ajoutee au JSON. Elle n'apparait PAS dans la liste
"Covariables a interroger" du dialogue de configuration ; sa propre case a
cocher juste au-dessus l'indique explicitement.
"""
from __future__ import annotations

import json
import time
from typing import Callable, Optional

from qgis.core import QgsBlockingNetworkRequest

from .collector import MAX_RETRIES, RETRY_DELAY, _request

ELEVATION_URL = "https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json"
ELEVATION_RESOURCE = "ign_rge_alti_wld"   # RGE ALTI : le MNT le plus precis couvrant la France
ELEVATION_COLUMN = "altitude_m"

Fetcher = Callable[[float, float], Optional[float]]


class ElevationError(RuntimeError):
    """La requete d'altitude a echoue (reseau ou reponse inattendue)."""


def default_fetcher(feedback=None) -> Fetcher:
    """Fetcher reel : requete HTTP directe (pas de couche, pas de geometrie
    en retour), en reutilisant juste le client HTTP bas niveau de collector.py."""

    def fetch(lon: float, lat: float) -> Optional[float]:
        params = {"lon": lon, "lat": lat, "resource": ELEVATION_RESOURCE, "zonly": "true"}
        last_error = "erreur inconnue"
        for attempt in range(1, MAX_RETRIES + 1):
            error, body, message = _request(ELEVATION_URL, params, feedback)
            if error == QgsBlockingNetworkRequest.NoError:
                try:
                    elevations = json.loads(body).get("elevations") or [None]
                    return elevations[0]
                except (ValueError, AttributeError) as exc:
                    last_error = f"reponse illisible : {exc}"
            else:
                last_error = message or f"code {error}"
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
        raise ElevationError(last_error)

    return fetch


def collect(lon: float, lat: float, fetch: Optional[Fetcher] = None, feedback=None) -> dict:
    """{ELEVATION_COLUMN: altitude en metres, ou None}. Leve ElevationError si la requete echoue."""
    fetch = fetch or default_fetcher(feedback)
    value = fetch(lon, lat)
    return {ELEVATION_COLUMN: value}
