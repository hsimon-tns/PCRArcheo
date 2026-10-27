import re
import math
import time
import json
import requests
import xmltodict
from enum import StrEnum
from typing import Optional
from shapely.geometry import shape, Point as ShapelyPoint

def loadJson(path):

    """Handles opening json file and returns data

    Returns:
        dict: loaded data
    """

    try:
        with open(path, encoding="utf-8") as f:   # sans cela : encodage système (cp1252 sous Windows), accents corrompus
            data = json.load(f)
            return data
    except FileNotFoundError as e:
        return str(e)
    except Exception as e:
        exit(e)

def checkKeys(data: dict, keys: list | tuple | set):

    """ Checks that a given dictionary contains all given keys

    Args:
        data (dict): the dictionary to inspect
        keys (list | tuple | set): keys to check in the given dictionary
    Returns:
        Returns a tuple, first indicates if all keys are found, second is the first missing if any, otherwise empty string
    """
    
    for k in keys:
        if k not in data:
            return False, k
    return True, ""

def checkCapaFormat(capa: dict, format: dict):
    """Checks the given capabilities data for its expected format

    Args:
        capa (dict): Capabilities data
        format (dict): Capabilities format

    Returns:
        bool: Wether the given capabilities matches the expected format
    """
    return True, "" #TODO
'''
    dType = type(capa)
    if dType != dict:
        return False, f"Capa type is {dType} (expected dict)"

    for k, v in format.items():
        if type(v) is str:
            vType = locate(v)
            if vType not in (int, str, tuple, list, set, dict):


    expKeys = ("date", "serveurs", "resume")
    found, missing = checkKeys(capa, expKeys)
    if not found:
        return found, f"Capa is missing key \"{missing}\""

    expServeurKeys = \
            ["id", "nom", "fournisseur", "type_service", "url", 
             "pertinence", "statut", "nb_couches", "couches",
             "erreur", "duree_ms"]

    servers = capa["serveurs"]
    if len(servers) == 0:
        return False, "Capa has no server data"

    #TODO : Test each server element 
    found, missing = checkKeys(capa["serveurs"][0], expServeurKeys)
    if not found:
        return found, f"Serveurs is missing key \"{missing}\""
    return True, ""
'''

BBOX_PATTERN = r"^\ *(((-)?\d{1,2}\.\d+\ *,\ *){3})((-)?\d{1,2}\.\d+)\ *$"
BBOX_REGEXP = re.compile(BBOX_PATTERN)
COORD_PATTERN = r"^\ *(((-)?\d{1,2}\.\d+\ *,\ *){1})((-)?\d{1,2}\.\d+)\ *$"
COORD_REGEXP = re.compile(COORD_PATTERN)
def checkBBOX(area: any, coords) -> bool:
    """Check that the given area contains the given point

    Args:
        area (any): A bouding box either in a string, iterable of dictionary format
                    Note that, as dictionary, keys must be "min_lon", "min_lat", "max_lon" and "max_lat"
        coords (any): GPS coordinates

    Returns:
        bool: Wether the coordinates are in bounds
    """
    if type(coords) is str:
        if not COORD_REGEXP.match(coords):
            raise ValueError(
                "String coordinates must be formated as : "
                "lon,lat (as decimal arithmetical values)")
        lon, lat = coords.replace(" ", "").split(",")
        lon = float(lon)
        lat = float(lat)
    elif hasattr(coords, '__iter__'):
        # Probably something like (lon, lat) or whatsoever
        if len(coords) != 2:
            raise ValueError(
                "Coordinates iterable must have a length of 2")
        lon, lat = coords
    else:
        raise TypeError(f"Coordinates of type {type(coords)} is not supported")
    if type(area) is dict:
        bbox = tuple(map(area.get, ["min_lon", "min_lat", "max_lon", "max_lat"], 4*[None]))
        if None in bbox:
            raise ValueError(
                "String bbox must be formated as dict with keys: "
                "min_lon,min_lat,max_lon,max_lat ")
    elif type(area) is str:
        if not BBOX_REGEXP.match(area):
            raise ValueError(
                "String bbox must be formated as : "
                "min_lon,min_lat,max_lon,max_lat "
                "(as decimal arithmetical values)")
        bbox = area.replace(" ", "").split(",")
    elif hasattr(area, "__iter__"):
        bbox = area
    bbox = [float(x) for x in bbox]

    min_x, min_y, max_x, max_y = bbox
    return not (lon < min_x or lon > max_x or lat < min_y or lat > max_y)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

class WFSREquest(StrEnum):
    GetCapabilities     = "GetCapabilities"
    GetFeature          = "GetFeature"
    DescribeFeatureType = "DescribeFeatureType"

TIMEOUT     = 20
RETRY_DELAY = 2
MAX_RETRIES = 3

def splitProjection(p: str) -> str:
    """Get the OGC projection format from the capabilities projection format

    Args:
        p (str): input format

    Returns:
        str: output format
    """
    m = re.search("(EPSG)|(IGNF)", p)
    if m:
        return p[m.start():]
    else:
        return ""


def send_request(url: str, params: dict) -> dict:
    """Send a OGC request

    Args:
        url (str): target server
        params (dict): set of parameters

    Returns:
        dict: _description_
    """
    params = dict(params)
    r = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)
            if r.status_code == 400 and "OUTPUTFORMAT" in params \
                    and b"permitted output format" in r.content:
                # Certains serveurs (BRGM/MapServer) refusent OUTPUTFORMAT=json
                # avec une erreur HTTP au lieu de retomber sur leur format par
                # defaut : on retire le parametre et on retente (verifie en prod).
                params.pop("OUTPUTFORMAT")
                r = requests.get(url, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            break
        except Exception as e:
            print(f"get({url}) tentative {attempt+1}/{MAX_RETRIES} echouee : "
                  f"{type(e).__name__}: {e}")
            r = None
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
    if r:
        print(r.url)
        h = r.headers
        fmt = h.get("content-type", "") + h.get("Content-Type", "")
        if fmt.startswith("application/xml"):
            #x = etree.fromstring(r.content)
            j = xmltodict.parse(r.content)
        elif fmt.startswith("text/xml"):
            j = xmltodict.parse(r.content)
        elif fmt.startswith("application/json"):
            j = r.json()
        else:
            print(f"Not handled : {fmt}")
            j = f"Not handled : {fmt}"
        return j
    return None


def getXMLasJSON(url: str, params: dict) -> Optional[dict]:
    """GET XML->dict avec retry ; les echecs sont journalises sur stdout."""
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return xmltodict.parse(r.content)
        except Exception as e:
            print(f"getXMLasJSON({url}) tentative {attempt+1}/{MAX_RETRIES} echouee : "
                  f"{type(e).__name__}: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
    return None

# ---------------------------------------------------------------------------
# Géométrie
# ---------------------------------------------------------------------------

def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Distance en km entre deux points WGS84."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi  = math.radians(lat2 - lat1)
    dlam  = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def find_containing_feature(features: list, lon: float, lat: float) -> list:
    """
    Retourne la feature dont le polygone contient (lon, lat).
    En pratique retourne une liste mais on espère qu'elle ne contienne qu'un élément
 
    Pipeline d'élimination progressive — du test le moins coûteux au plus précis :
      Niveau 1 : BBOX individuelle du polygone     O(1)  sans Shapely
      Niveau 2 : Rayon englobant (centroïde)       O(1)  sans Shapely
      Niveau 3 : Point-in-polygon exact (Shapely)  O(n)  seulement sur les survivants
 
    En pratique, niveaux 1 et 2 éliminent la majorité des candidats
    sans jamais construire d'objet Shapely.
    """
    pt_shapely = None   # construit en lazy seulement si niveau 3 nécessaire
    ret = []
 
    for feat in features:
        geom_json = feat.get("geometry", {})
        coords    = geom_json.get("coordinates", [])
        if not coords:
            continue
 
        # ── Niveau 1 : BBOX individuelle ──────────────────────────────────
        # Calculer min/max lon/lat depuis les coordonnées brutes sans Shapely
        def _all_points(c):
            """Aplatit récursivement les coordonnées en paires (x, y)."""
            if not c:
                return
            if isinstance(c[0], (int, float)):
                yield c[0], c[1]
            else:
                for sub in c:
                    yield from _all_points(sub)
 
        pts = list(_all_points(coords))
        if not pts:
            continue
 
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
 
        if not (min_x <= lon <= max_x and min_y <= lat <= max_y):
            # Le point est hors de la BBOX individuelle → élimination immédiate
            continue
 
        # ── Niveau 2 : rayon englobant depuis le centroïde ────────────────
        # Centroïde approximatif = moyenne des sommets (assez précis pour un filtre)
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        dist_centroid = haversine_km(lon, lat, cx, cy)
 
        # Rayon englobant = distance centroïde → sommet le plus éloigné
        rayon_km = max(haversine_km(cx, cy, x, y) for x, y in pts)
 
        if dist_centroid > rayon_km * 1.05:
            # 5% de marge pour les approximations de centroïde
            # Le point ne peut pas être dans le polygone → élimination
            continue
 
        # ── Niveau 3 : point-in-polygon exact (Shapely) ───────────────────
        # Seulement ici on construit l'objet Shapely
        if pt_shapely is None:
            pt_shapely = ShapelyPoint(lon, lat)
        try:
            geom = shape(geom_json)
            if geom.contains(pt_shapely):
                ret.append(feat)
        except Exception as e:
            print(f"find_containing_feature: geometrie invalide ignoree : "
                  f"{type(e).__name__}: {e}")
            continue
 
    # Aucun polygone ne contient exactement le point
    # → fallback : centroïde le plus proche parmi les survivants des niveaux 1 et 2
    return ret
