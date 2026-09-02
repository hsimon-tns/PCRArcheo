import re
import math
from typing import Optional
import requests
import time
import xmltodict
import json
from enum import StrEnum
from shapely.geometry import shape, Point as ShapelyPoint

def checkKeys(data, keys):
    dKeys = data.keys()
    for k in keys:
        if k not in dKeys:
            return False, k
    return True, ""

def checkCapaFormat(capa):
    dType = type(capa)
    if dType != dict:
        return False, f"Capa type is {dType} (expected dict)"

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

BBOX_PATTERN = r"^\ *(((-)?\d{1,2}\.\d+\ *,\ *){3})((-)?\d{1,2}\.\d+)\ *$"
BBOX_REGEXP = re.compile(BBOX_PATTERN)
COORD_PATTERN = r"^\ *(((-)?\d{1,2}\.\d+\ *,\ *){1})((-)?\d{1,2}\.\d+)\ *$"
COORD_REGEXP = re.compile(COORD_PATTERN)
def checkBBOX(emprise, coords):
    if type(coords) is str:
        if not COORD_REGEXP.match(coords):
            raise ValueError(
                "String coordinates must be formated as : "
                "lon,lat (as decimal arithmetical values)")
        lat, lon = coords.replace(" ", "").split(",")
        lat = float(lat)
        lon = float(lon)
    elif hasattr(coords, '__iter__'):
        # Probably something like (lon, lat) or whatsoever
        if len(coords) != 2:
            raise ValueError(
                "Coordinates iterable must have a length of 2")
        lat, lon = coords
    else:
        raise TypeError(f"Coordinates of type {type(coords)} is not supported")
    if type(emprise) is dict:
        bbox = (
            emprise["min_lon"], emprise["min_lat"],
            emprise["max_lon"], emprise["max_lat"])
    elif type(emprise) is str:
        if not BBOX_REGEXP.match(emprise):
            raise ValueError(
                "String bbox must be formated as : "
                "min_lon,min_lat,max_lon,max_lat "
                "(as decimal arithmetical values)")
        bbox = emprise.replace(" ", "").split(",")
        print(f"bbox sqdqsfd = {bbox}")
    elif hasattr(emprise, "__iter__"):
        bbox = emprise
    bbox = [float(x) for x in bbox]

    print(f"bbox={bbox}")
    min_x, min_y, max_x, max_y = bbox
    return not (lat < min_x or lat > max_x or lon < min_y or lon > max_y)

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
BBOX_DEG    = 0.05   # rayon par défaut en degrés (~5km) pour intersection_bbox

def feat_coords(feat: dict) -> tuple:
    return feat.get("geometry", {}).get("coordinates", [])

def _get(url: str, params: dict) -> Optional[dict]:
    """GET JSON avec retry silencieux."""
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            j = r.json()
            if not j:
                s = xmltodict.parse(r.content)
                j = json.dumps(s)
            return j
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
    return None

def getXMLasJSON(url: str, params: dict) -> Optional[dict]:
    """GET JSON avec retry silencieux."""
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return xmltodict.parse(r.content)
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
    return None

# ---------------------------------------------------------------------------
# Calcul de distance
# ---------------------------------------------------------------------------

def _haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Distance en km entre deux points WGS84."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi  = math.radians(lat2 - lat1)
    dlam  = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def _closest_point_distance(lon: float, lat: float, feature: dict) -> float:
    """
    Distance minimale entre (lon, lat) et tous les points d'une géométrie GeoJSON.
    Fonctionne pour Point, LineString, MultiLineString, Polygon, MultiPolygon.
    """
    geom  = feature.get("geometry", {})

    gtype = geom.get("type", "")
    coords = geom.get("coordinates", [])

    def flatten(c, depth=0):
        """Aplatit récursivement les coordonnées en liste de [lon, lat]."""
        if not c:
            return []
        if isinstance(c[0], (int, float)):
            return [c]
        return [pt for sub in c for pt in flatten(sub, depth + 1)]

    points = flatten(coords)
    if not points:
        return float("inf")
    return min(_haversine_km(lon, lat, pt[0], pt[1]) for pt in points)

# ---------------------------------------------------------------------------
# Construction des paramètres WFS depuis une couche de couches_selection.json
# ---------------------------------------------------------------------------

def _build_wfs_params(
    layer: dict,
    lon: float,
    lat: float,
    bbox_deg: float = BBOX_DEG,
    count: int = 10,
) -> dict:
    """
    Construit le dict de paramètres WFS à partir d'une entrée couches_selection.

    Paramètres WFS utilisés :
        SERVICE      → toujours "WFS"
        VERSION      → toujours "2.0.0"
        REQUEST      → toujours "GetFeature"
        TYPENAMES    → layer["couche_nom"]   (nom de la couche)
        SRSNAME      → "EPSG:4326"           (projection de la réponse)
        OUTPUTFORMAT → "application/json"
        BBOX         → calculé depuis lon/lat du site + bbox_deg
        PROPERTYNAME → layer["champs_cibles"] (colonnes à récupérer)
        COUNT        → nombre max de résultats
    """
    # BBOX format WFS 2.0 : minLat,minLon,maxLat,maxLon,CRS
    bbox = f"{lat-bbox_deg},{lon-bbox_deg},{lat+bbox_deg},{lon+bbox_deg},EPSG:4326"

    params = {
        "SERVICE":      "WFS",
        "VERSION":      "2.0.0",
        "REQUEST":      "GetFeature",
        "TYPENAMES":    layer["couche_nom"],
        "SRSNAME":      "EPSG:4326",
        "OUTPUTFORMAT": "application/json",
        "BBOX":         bbox,
        "COUNT":        str(count),
    }

    # PROPERTYNAME : demander uniquement les champs utiles (allège la réponse)
    champs = layer.get("champs_cibles", [])
    if champs:
        params["PROPERTYNAME"] = ",".join(champs)

    return params

def _find_containing_feature(features: list, lon: float, lat: float) -> list:
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
        dist_centroid = _haversine_km(lon, lat, cx, cy)
 
        # Rayon englobant = distance centroïde → sommet le plus éloigné
        rayon_km = max(_haversine_km(cx, cy, x, y) for x, y in pts)
 
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
        except Exception:
            continue
 
    # Aucun polygone ne contient exactement le point
    # → fallback : centroïde le plus proche parmi les survivants des niveaux 1 et 2
    return ret

# ---------------------------------------------------------------------------
# Les trois types de requête
# ---------------------------------------------------------------------------

def query_intersection_point(
    layer: dict,
    lon: float,
    lat: float,
) -> dict:
    """
    Récupère la valeur de la couche au point (ou dans un rayon immédiat).
    Retourne les champs_cibles de la feature la plus proche.
    Usage : type de sol, lithologie, aléa argile...
    """
    covariable = layer["covariable_cible"]
    champs     = layer.get("champs_cibles", [])
    empty      = {covariable: None}, None

    params = _build_wfs_params(layer, lon, lat, bbox_deg=0.01, count=5)
    data   = _get(layer["url"], params)
    if not data:
        print("\tData was empty...")
        return empty
    print(f"\tReturned features : {data["numberReturned"]}")

    features = data.get("features", [])
    if not features:
        print("\tAugmentation du rayon de recherche")
        # Élargir le rayon si rien trouvé
        params = _build_wfs_params(layer, lon, lat, bbox_deg=0.05, count=5)
        data   = _get(layer["url"], params)
        if not data:
            print("\tData was empty...")
            return empty
        features = data.get("features", [])

    if not features:
        print("\tToujours pas de donnees")
        return empty

    print(f"\nData ok ! ({len(features)} features)")
    print("\nTraitement des features...")
    # Prendre la feature la plus proche
    best = _find_containing_feature(features, lon, lat)
    best = min(features, key=lambda f: _closest_point_distance(lon, lat, f))
    props = best.get("properties", {})
    coords = feat_coords(best)

    # Si un seul champ cible → valeur directe
    # Si plusieurs → dict préfixé par covariable
    if len(champs) == 1:
        return {covariable: props.get(champs[0])}, coords
    elif champs:
        return {f"{covariable}__{c}": props.get(c) for c in champs}, coords
    else:
        # Aucun champ spécifié → retourner toutes les propriétés
        return {f"{covariable}__{k}": v for k, v in props.items()}, coords

def query_intersection_bbox(
    layer: dict,
    lon: float,
    lat: float,
) -> dict:
    """
    Trouve la feature la plus proche dans un rayon et calcule la distance.
    Retourne : distance en km + valeurs des champs_cibles.
    Usage : cours d'eau, tronçons hydrographiques...
    """
    covariable = layer["covariable_cible"]
    champs     = layer.get("champs_cibles", [])
    empty      = {covariable: None}, None
    for c in champs:
        empty[f"{covariable}__{c}"] = None

    params = _build_wfs_params(layer, lon, lat, bbox_deg=BBOX_DEG, count=10)
    data   = _get(layer["url"], params)
    if not data:
        return empty

    features = data.get("features", [])
    if not features:
        # Élargir à ~10km
        params = _build_wfs_params(layer, lon, lat, bbox_deg=0.1, count=10)
        data   = _get(layer["url"], params)
        if not data:
            return empty
        features = data.get("features", [])

    if not features:
        return empty

    # Trouver la feature la plus proche
    best_dist = float("inf")
    best_feat = None
    best_coord = []
    for feat in features:
        d = _closest_point_distance(lon, lat, feat)
        if d < best_dist:
            best_dist = d
            best_feat = feat
            best_coord = feat_coords(d)

    if best_feat is None:
        return empty

    props  = best_feat.get("properties", {})
    result = {covariable: round(best_dist, 4)}
    for c in champs:
        result[f"{covariable}__{c}"] = props.get(c)

    return result, best_coord

def query_count_radius(
    layer: dict,
    lon: float,
    lat: float,
) -> dict:
    """
    Compte le nombre de features dans un rayon donné.
    Usage : mouvements de terrain, cavités...
    """
    covariable = layer["covariable_cible"]
    rayon_m    = layer.get("rayon_m", 5000)
    rayon_deg  = rayon_m / 111_000  # approximation : 1° ≈ 111km

    params = _build_wfs_params(layer, lon, lat, bbox_deg=rayon_deg, count=1000)
    # Retirer PROPERTYNAME pour le comptage (on veut juste le nombre)
    params.pop("PROPERTYNAME", None)

    data = _get(layer["url"], params)
    if not data:
        return {covariable: None}, None

    features = data.get("features", [])

    # Filtrer ceux réellement dans le rayon (la BBOX est un carré, pas un cercle)
    rayon_km = rayon_m / 1000
    dans_rayon = [
        f for f in features
        if _closest_point_distance(lon, lat, f) <= rayon_km
    ]

    return {covariable: len(dans_rayon)}, []
