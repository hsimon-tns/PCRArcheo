"""
pcr_tools/env_collector.py
--------------------------
Collecte automatique de covariables environnementales pour une liste de sites
archéologiques, pilotée par couches_selection.json.

Le fichier couches_selection.json définit quoi chercher et où.
Ce module sait uniquement comment interroger un serveur WFS et calculer
une distance ou extraire une valeur — il n'a pas besoin de connaître
les couches à l'avance.

Trois types de requête (définis dans couches_selection.json) :
  intersection_point  → valeur de la couche au point exact (ex: type de sol)
  intersection_bbox   → features dans un rayon, on calcule la distance min
  count_radius        → nombre de features dans un rayon (ex: mouvements terrain)

Entrée  : CSV [id, lon, lat] + couches_selection.json
Sortie  : CSV enrichi avec une colonne par covariable_cible

Usage :
    from pcr_tools.env_collector import collect_covariates
    df = collect_covariates("sites.csv", "couches_selection.json")
    df.to_csv("sites_covariables.csv", index=False)

    # CLI :
    python -m pcr_tools.env_collector sites.csv couches_selection.json -o sites_covariables.csv -v
    python -m pcr_tools.env_collector sites.csv couches_selection.json --example
"""

import json
import math
import time
from pathlib import Path
from typing import Optional

import requests
import pandas as pd
from shapely.geometry import shape, Point as ShapelyPoint


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

TIMEOUT     = 20
RETRY_DELAY = 2
MAX_RETRIES = 3
BBOX_DEG    = 0.05   # rayon par défaut en degrés (~5km) pour intersection_bbox


# ---------------------------------------------------------------------------
# Utilitaires réseau
# ---------------------------------------------------------------------------

def _get(url: str, params: dict) -> Optional[dict]:
    """GET JSON avec retry silencieux."""
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
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
    # BBOX format WFS 2.0 : minLat,minLon,maxLat,maxLon,CRS. La forme courte
    # "EPSG:4326" est lue lon,lat par l'IGN (GeoServer) mais lat,lon par le
    # BRGM (MapServer) : avec l'ordre ci-dessus, l'IGN renvoie 0 entite. La
    # forme URN est sans ambiguite et acceptee par les deux (verifie en prod).
    bbox = f"{lat-bbox_deg},{lon-bbox_deg},{lat+bbox_deg},{lon+bbox_deg},urn:ogc:def:crs:EPSG::4326"

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

    # PROPERTYNAME est volontairement absent : le demander fait que le serveur
    # renvoie les attributs SANS la geometrie, or les trois strategies de
    # requete (point-in-polygon, distance, comptage) ont toutes besoin de la
    # geometrie de chaque feature en plus de ses attributs (verifie en prod).

    return params


# ---------------------------------------------------------------------------
# Les trois types de requête
# ---------------------------------------------------------------------------

def _find_containing_feature(features: list, lon: float, lat: float) -> dict | None:
    """
    Retourne la feature dont le polygone contient (lon, lat).

    Pipeline d'élimination progressive — du test le moins coûteux au plus précis :
      Niveau 1 : BBOX individuelle du polygone     O(1)  sans Shapely
      Niveau 2 : Rayon englobant (centroïde)       O(1)  sans Shapely
      Niveau 3 : Point-in-polygon exact (Shapely)  O(n)  seulement sur les survivants

    En pratique, niveaux 1 et 2 éliminent la majorité des candidats
    sans jamais construire d'objet Shapely.
    Si aucun polygone ne contient le point, retourne le plus proche par centroïde
    (fallback pour les points sur une frontière ou avec léger décalage de projection).
    """
    pt_shapely = None   # construit en lazy seulement si niveau 3 nécessaire
    fallback_dist = float("inf")
    fallback_feat = None

    for feat in features:
        geom_json = feat.get("geometry", {})
        gtype     = geom_json.get("type", "")
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

        # Candidat pour le fallback (centroïde le plus proche)
        if dist_centroid < fallback_dist:
            fallback_dist = dist_centroid
            fallback_feat = feat

        # ── Niveau 3 : point-in-polygon exact (Shapely) ───────────────────
        # Seulement ici on construit l'objet Shapely
        if pt_shapely is None:
            pt_shapely = ShapelyPoint(lon, lat)
        try:
            geom = shape(geom_json)
            if geom.contains(pt_shapely):
                return feat   # trouvé — on s'arrête immédiatement
        except Exception:
            continue

    # Aucun polygone ne contient exactement le point
    # → fallback : centroïde le plus proche parmi les survivants des niveaux 1 et 2
    return fallback_feat


def _query_intersection_point(
    layer: dict,
    lon: float,
    lat: float,
) -> dict:
    """
    Récupère la valeur de la couche polygonale au point exact.

    Utilise un test point-in-polygon (Shapely) plutôt qu'une distance,
    ce qui est correct pour les polygones concaves (occupation du sol,
    géologie, aléa argile...) dont la bounding box peut contenir le
    point sans que le polygone réel le contienne.

    Usage : type de sol, lithologie, aléa argile, occupation du sol...
    """
    covariable = layer["covariable_cible"]
    champs     = layer.get("champs_cibles", [])
    empty      = {covariable: None}
    if len(champs) > 1:
        for c in champs:
            empty[f"{covariable}__{c}"] = None

    # Requête avec une petite BBOX d'abord
    params = _build_wfs_params(layer, lon, lat, bbox_deg=0.01, count=20)
    data   = _get(layer["url"], params)
    features = (data or {}).get("features", [])

    if not features:
        # Élargir si rien trouvé (zones peu denses comme la géologie)
        params = _build_wfs_params(layer, lon, lat, bbox_deg=0.05, count=20)
        data   = _get(layer["url"], params)
        features = (data or {}).get("features", [])

    if not features:
        return empty

    best = _find_containing_feature(features, lon, lat)
    if best is None:
        return empty

    props = best.get("properties", {})

    if len(champs) == 1:
        return {covariable: props.get(champs[0])}
    elif champs:
        return {f"{covariable}__{c}": props.get(c) for c in champs}
    else:
        return {f"{covariable}__{k}": v for k, v in props.items()}


def _query_intersection_bbox(
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
    empty      = {covariable: None}
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
    for feat in features:
        d = _closest_point_distance(lon, lat, feat)
        if d < best_dist:
            best_dist = d
            best_feat = feat

    if best_feat is None:
        return empty

    props  = best_feat.get("properties", {})
    result = {covariable: round(best_dist, 4)}
    for c in champs:
        result[f"{covariable}__{c}"] = props.get(c)

    return result


def _query_count_radius(
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
        return {covariable: None}

    features = data.get("features", [])

    # Filtrer ceux réellement dans le rayon circulaire
    # Pour les polygones : on prend le centroïde comme référence de distance
    # (on compte les événements ponctuels dans un rayon, pas les zones)
    rayon_km = rayon_m / 1000
    pt = ShapelyPoint(lon, lat)
    dans_rayon = []
    for f in features:
        try:
            geom = shape(f["geometry"])
            # Pour un Point, distance directe ; pour un Polygon, distance au centroïde
            ref = geom if geom.geom_type == "Point" else geom.centroid
            d = _haversine_km(lon, lat, ref.x, ref.y)
            if d <= rayon_km:
                dans_rayon.append(f)
        except Exception:
            # Fallback sur la méthode de distance par points si Shapely échoue
            if _closest_point_distance(lon, lat, f) <= rayon_km:
                dans_rayon.append(f)

    return {covariable: len(dans_rayon)}


# ---------------------------------------------------------------------------
# Routeur : appelle le bon type de requête selon layer["type_requete"]
# ---------------------------------------------------------------------------

QUERY_TYPES = {
    "intersection_point": _query_intersection_point,
    "intersection_bbox":  _query_intersection_bbox,
    "count_radius":       _query_count_radius,
}

def _query_layer(layer: dict, lon: float, lat: float) -> dict:
    """Appelle le type de requête défini dans la couche."""
    type_requete = layer.get("type_requete", "intersection_point")
    fn = QUERY_TYPES.get(type_requete)
    if fn is None:
        raise ValueError(f"type_requete inconnu : '{type_requete}'. "
                         f"Valeurs possibles : {list(QUERY_TYPES)}")
    return fn(layer, lon, lat)


# ---------------------------------------------------------------------------
# Point d'entrée principal
# ---------------------------------------------------------------------------

def collect_covariates(
    input_csv: str,
    selection_json: str,
    output_csv: Optional[str] = None,
    id_col:  str = "id",
    lon_col: str = "lon",
    lat_col: str = "lat",
    couches_ids: Optional[list[str]] = None,
    verbose: bool = False,
) -> pd.DataFrame:
    """
    Collecte les covariables environnementales pour chaque site du CSV d'entrée.

    Paramètres
    ----------
    input_csv : str
        CSV avec colonnes [id, lon, lat].
    selection_json : str
        Chemin vers couches_selection.json.
    output_csv : str, optional
        CSV de sortie enrichi.
    id_col, lon_col, lat_col : str
        Noms des colonnes dans le CSV.
    couches_ids : list[str], optional
        Sous-ensemble de couche_nom à utiliser. Si None, toutes les couches.
    verbose : bool

    Retourne
    --------
    pd.DataFrame avec une colonne par covariable_cible.
    """
    # Chargement des entrées
    df     = pd.read_csv(input_csv)
    layers = json.loads(Path(selection_json).read_text(encoding="utf-8"))

    # Validation colonnes
    missing = {id_col, lon_col, lat_col} - set(df.columns)
    if missing:
        raise ValueError(f"Colonnes manquantes dans le CSV : {missing}")

    # Filtrage optionnel des couches
    if couches_ids:
        layers = [l for l in layers if l["couche_nom"] in couches_ids]
        if not layers:
            raise ValueError(f"Aucune couche trouvée pour : {couches_ids}")

    if verbose:
        print(f"Sites     : {len(df)}")
        print(f"Couches   : {len(layers)}")
        for l in layers:
            print(f"  [{l['type_requete']:20s}] {l['couche_nom']}  →  {l['covariable_cible']}")
        print()

    results = []
    n = len(df)

    for _, row in df.iterrows():
        site_id = row[id_col]
        lon     = float(row[lon_col])
        lat     = float(row[lat_col])
        covars  = {id_col: site_id, lon_col: lon, lat_col: lat}

        if verbose:
            print(f"[{_ + 1}/{n}] {site_id}  ({lon:.4f}, {lat:.4f})")

        for layer in layers:
            nom = layer["couche_nom"]
            if verbose:
                print(f"  → {nom}...", end=" ", flush=True)
            try:
                result = _query_layer(layer, lon, lat)
                covars.update(result)
                if verbose:
                    vals = list(result.values())
                    print(vals[0] if len(vals) == 1 else vals)
            except Exception as e:
                if verbose:
                    print(f"erreur : {e}")
                covars[layer["covariable_cible"]] = None

            time.sleep(0.2)  # politesse envers les APIs publiques

        results.append(covars)

    out_df = pd.DataFrame(results)

    if output_csv:
        out_df.to_csv(output_csv, index=False, encoding="utf-8")
        if verbose:
            print(f"\n✓ {len(out_df)} sites  ·  {len(out_df.columns)} colonnes  →  {output_csv}")

    return out_df


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    EXAMPLE_CSV = """id,lon,lat
EA001,-1.5536,47.2184
EA002,-1.4812,47.2601
EA003,-1.5023,47.3041
"""

    parser = argparse.ArgumentParser(
        description="Collecte de covariables environnementales (piloté par couches_selection.json)"
    )
    parser.add_argument("csv",      nargs="?", help="CSV d'entrée [id, lon, lat]")
    parser.add_argument("selection", nargs="?", help="couches_selection.json")
    parser.add_argument("--output",  "-o",  help="CSV de sortie enrichi")
    parser.add_argument("--id-col",  default="id")
    parser.add_argument("--lon-col", default="lon")
    parser.add_argument("--lat-col", default="lat")
    parser.add_argument("--couches", "-c",  help="Couches à activer (virgule-séparées par couche_nom)")
    parser.add_argument("--example", action="store_true", help="Créer un CSV d'exemple")
    parser.add_argument("--verbose", "-v",  action="store_true")
    args = parser.parse_args()

    if args.example:
        Path("sites_exemple.csv").write_text(EXAMPLE_CSV, encoding="utf-8")
        print("CSV d'exemple créé : sites_exemple.csv")
    elif not args.csv or not args.selection:
        parser.print_help()
    else:
        couches_ids = [c.strip() for c in args.couches.split(",")] if args.couches else None
        df = collect_covariates(
            args.csv,
            args.selection,
            output_csv=args.output,
            id_col=args.id_col,
            lon_col=args.lon_col,
            lat_col=args.lat_col,
            couches_ids=couches_ids,
            verbose=args.verbose,
        )
        if not args.output:
            print(df.to_string())
