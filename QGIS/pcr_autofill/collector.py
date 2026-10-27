"""WFS queries computing the covariates of one location.

Everything here is safe to run in a worker thread : no layer, project or GUI access.

Design choices, all verified against the live IGN and BRGM servers :

* BBOX is sent as ``minLat,minLon,maxLat,maxLon,urn:ogc:def:crs:EPSG::4326``. The short form
  ``...,EPSG:4326`` is read as lon/lat by IGN (GeoServer) but as lat/lon by BRGM (MapServer); the
  URN form is unambiguous and accepted by both.
* PROPERTYNAME is never sent : it makes the server drop the geometry, which every strategy needs,
  and one invalid attribute name would fail the whole request. Target fields are picked from the
  full response instead (a missing one simply yields NULL).
* GeoJSON is requested first ; servers that refuse it (BRGM) are queried in their default format
  (GML). Both are parsed with GDAL/OGR, so no third-party package (shapely, xmltodict) is needed.
* Distances are measured in an azimuthal-equidistant projection centred on the query point, i.e.
  to the nearest *point of the geometry* in true metres, not to the nearest vertex.
"""
from __future__ import annotations

import contextlib
import math
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

from osgeo import gdal, ogr
from qgis.core import (
    QgsBlockingNetworkRequest, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsCoordinateTransformContext, QgsGeometry, QgsPointXY, QgsWkbTypes,
)
from qgis.PyQt.QtCore import QUrl, QUrlQuery
from qgis.PyQt.QtNetwork import QNetworkRequest

from .covariates import QUERY_COUNT, QUERY_NEAREST, QUERY_POINT, Covariate
from .logger import log, warn

URN_4326 = "urn:ogc:def:crs:EPSG::4326"
M_PER_DEG = 111_320.0

MAX_RETRIES = 3
RETRY_DELAY = 2.0
REQUEST_TIMEOUT_MS = 30_000

POINT_EXACT_M = 1            # window so small that only polygons containing the point qualify
POINT_TOLERANCE_M = 10.0     # a polygon this close to the point counts as containing it
NEAREST_RADII_M = (500, 2_000, 5_000, 11_000)   # growing search windows (nearest feature)
NEAREST_COUNT = 200
COUNT_MAX = 1000


class WfsError(RuntimeError):
    """A WFS request failed (network, server exception or unreadable response)."""


@dataclass
class Feature:
    geometry: Optional[QgsGeometry]
    properties: dict


Fetcher = Callable[[Covariate, dict], list]


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

_JSON_REFUSED = set()   # (url, type name) of layers whose server refuses application/json


def _exception_text(body: bytes) -> Optional[str]:
    """Message of an OWS ExceptionReport, if *body* is one."""
    if not body.lstrip().startswith(b"<"):
        return None
    match = re.search(rb"<(?:ows:)?ExceptionText>(.*?)</(?:ows:)?ExceptionText>", body, re.S)
    if match:
        return re.sub(r"\s+", " ", match.group(1).decode("utf-8", "replace")).strip()
    if b"ExceptionReport" in body:
        return "ExceptionReport"
    return None


def _request(url: str, params: dict, feedback=None):
    query = QUrlQuery()
    for key, value in params.items():
        query.addQueryItem(key, str(value))
    qurl = QUrl(url)
    qurl.setQuery(query)
    request = QNetworkRequest(qurl)
    if hasattr(request, "setTransferTimeout"):   # Qt >= 5.15 : QGIS' default is 60 s
        request.setTransferTimeout(REQUEST_TIMEOUT_MS)
    blocking = QgsBlockingNetworkRequest()   # honours QGIS' proxy / authentication / SSL settings
    error = blocking.get(request, True, feedback)
    reply = blocking.reply()
    return error, bytes(reply.content()), reply.errorString()


_BAD_PROPERTY = re.compile(r"(?:Requested property:|Invalid PROPERTYNAME)\s*([^\s,;]+)", re.I)
_REJECTED_PROPERTIES = {}   # (url, type name) -> lower-cased attribute names the server does not know


def _drop_property_names(params: dict, unknown) -> dict:
    """*params* without the attribute names in *unknown* (lower-cased) ; PROPERTYNAME goes once empty."""
    remaining = [name for name in params["PROPERTYNAME"].split(",") if name.lower() not in unknown]
    pruned = {name: value for name, value in params.items() if name != "PROPERTYNAME"}
    if remaining:
        pruned["PROPERTYNAME"] = ",".join(remaining)
    return pruned


def _no_valid_field(key, asked_names: str) -> WfsError:
    return WfsError(f"aucun des champs demandés n'existe dans {key[1]} : {asked_names.replace(',', ', ')} "
                    "(à corriger dans champs_cibles)")


def get_features(url: str, params: dict, feedback=None) -> list:
    """GetFeature request -> list of Feature.

    Adapts to what the server accepts : a refused GeoJSON output falls back to the server's default
    format (GML), and an unknown attribute in PROPERTYNAME is dropped (and remembered for this layer,
    so the mistake in the configuration costs one failed request per session, not one per feature).
    Adapting is not failing. Transient failures - network error, server exception, empty or
    unreadable answer - are retried.
    """
    params = dict(params)
    key = (url, params.get("TYPENAMES"))
    rejected = _REJECTED_PROPERTIES.setdefault(key, set())
    asked_names = params.get("PROPERTYNAME")
    if asked_names and rejected:
        params = _drop_property_names(params, rejected)
        if "PROPERTYNAME" not in params:
            raise _no_valid_field(key, asked_names)
    last_error, failures = "erreur inconnue", 0
    while True:
        if feedback is not None and feedback.isCanceled():
            raise WfsError("annulé")
        query = dict(params)
        if key not in _JSON_REFUSED:
            query["OUTPUTFORMAT"] = "application/json"
        error, body, message = _request(url, query, feedback)
        text = _exception_text(body)
        if text and "OUTPUTFORMAT" in query and "permitted output format" in text:
            _JSON_REFUSED.add(key)
            continue
        if text and "PROPERTYNAME" in params and "propert" in text.lower():
            asked = {name.lower() for name in params["PROPERTYNAME"].split(",")}
            named = _BAD_PROPERTY.search(text)
            if named and named.group(1).lower() in asked:
                rejected.add(named.group(1).lower())
                warn(f"{key[1]} : attribut « {named.group(1)} » inconnu du serveur, ignoré "
                     "(à corriger dans champs_cibles)")
                params = _drop_property_names(params, rejected)
                if "PROPERTYNAME" not in params:   # nothing valid left : fetching the feature would be futile
                    raise _no_valid_field(key, asked_names)
            else:   # the message does not say which one : ask for the whole feature
                params = {name: value for name, value in params.items() if name != "PROPERTYNAME"}
            continue
        if error == QgsBlockingNetworkRequest.NoError and not text:
            try:
                return parse_features(body)
            except WfsError as exc:
                last_error = str(exc)
        else:
            last_error = text or message or f"code {error}"
        failures += 1
        if failures >= MAX_RETRIES:
            raise WfsError(last_error)
        time.sleep(RETRY_DELAY)


def _explicit_no_exceptions():
    """Scope in which OGR reports errors by return value : GDAL's default, stated explicitly.

    Silences GDAL's "neither UseExceptions nor DontUseExceptions was called" FutureWarning without
    touching the process-wide setting, which other plugins may rely on.
    """
    manager = getattr(ogr, "ExceptionMgr", None)   # GDAL >= 3.7
    return manager(useExceptions=False) if manager else contextlib.nullcontext()


_PARSE_LOCK = threading.Lock()


def parse_features(body: bytes) -> list:
    """GeoJSON or GML bytes -> list of Feature (2D geometries, WGS84 lon/lat).

    Serialised : the exception mode of OGR is process-wide state, and concurrent enter / exit of
    _explicit_no_exceptions() could leave it changed for the other plugins. Parsing takes
    milliseconds ; the network is what is slow.
    """
    with _PARSE_LOCK, _explicit_no_exceptions():
        return _parse_features(body)


def _parse_features(body: bytes) -> list:
    name = f"/vsimem/pcr_{uuid.uuid4().hex}"
    gdal.FileFromMemBuffer(name, body)
    gdal.PushErrorHandler("CPLQuietErrorHandler")
    try:
        dataset = ogr.Open(name)
        if dataset is None:
            snippet = re.sub(r"\s+", " ", body[:120].decode("utf-8", "replace"))
            raise WfsError(f"réponse illisible : {snippet}")
        features = []
        for index in range(dataset.GetLayerCount()):
            for item in dataset.GetLayer(index):
                geometry = None
                ogr_geometry = item.GetGeometryRef()
                if ogr_geometry is not None and not ogr_geometry.IsEmpty():
                    ogr_geometry = ogr_geometry.Clone()
                    ogr_geometry.FlattenTo2D()
                    geometry = QgsGeometry.fromWkt(ogr_geometry.ExportToWkt())
                    if geometry.isNull():
                        geometry = None
                features.append(Feature(geometry, dict(item.items())))
        dataset = None
        return features
    finally:
        gdal.PopErrorHandler()
        for suffix in ("", ".gfs", ".xsd"):
            gdal.Unlink(name + suffix)


def default_fetcher(feedback=None) -> Fetcher:
    return lambda covariate, params: get_features(covariate.url, params, feedback)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

class LocalFrame:
    """Azimuthal-equidistant projection centred on a lon/lat point.

    Every distance from the centre is a true geodesic distance in metres, so the distance from
    the centre to a transformed geometry is the true distance to its nearest point.
    """

    def __init__(self, lon: float, lat: float):
        crs = QgsCoordinateReferenceSystem.fromProj(
            f"+proj=aeqd +lat_0={lat} +lon_0={lon} +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs")
        self._transform = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem("EPSG:4326"), crs, QgsCoordinateTransformContext())
        self._origin = QgsGeometry.fromPointXY(QgsPointXY(0.0, 0.0))

    def distance_m(self, geometry: QgsGeometry) -> float:
        projected = QgsGeometry(geometry)
        if projected.transform(self._transform) != 0:
            return math.inf
        return projected.distance(self._origin)


def _window_params(covariate: Covariate, lon: float, lat: float, radius_m: float, count: int) -> dict:
    """GetFeature parameters for a window inscribing the circle of *radius_m* around the point."""
    half_lat = radius_m / M_PER_DEG
    half_lon = radius_m / (M_PER_DEG * max(math.cos(math.radians(lat)), 0.01))
    return {
        "SERVICE": "WFS",
        "VERSION": "2.0.0",
        "REQUEST": "GetFeature",
        "TYPENAMES": covariate.layer_name,
        "SRSNAME": "EPSG:4326",
        "BBOX": f"{lat - half_lat},{lon - half_lon},{lat + half_lat},{lon + half_lon},{URN_4326}",
        "COUNT": str(count),
    }


def _attribute(feature: Feature, name: str):
    """Attribute value by name (exact match first, then case-insensitive)."""
    properties = feature.properties
    if name in properties:
        return properties[name]
    lowered = name.lower()
    for key, value in properties.items():
        if key.lower() == lowered:
            return value
    return None


def _field_values(covariate: Covariate, feature: Optional[Feature]) -> dict:
    values = {}
    for name in covariate.fields:
        value = _attribute(feature, name) if feature is not None else None
        values[covariate.field_column(name)] = None if value is None else str(value)
    return values


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

def query_point(covariate: Covariate, lon: float, lat: float, fetch: Fetcher) -> dict:
    """Attributes of the polygon containing the point (soil type, lithology, clay hazard...).

    Stage 1 lets the server do the point-in-polygon : a window of about a metre only matches the
    polygon(s) containing the point, and with PROPERTYNAME the answer holds attributes alone. These
    layers have huge geometries (megabytes for a single polygon), so this is the difference between
    seconds and minutes. Stage 2, only if stage 1 found nothing, fetches the geometries within a few
    metres to accept a point sitting on a border or slightly off.
    """
    stage_1 = _window_params(covariate, lon, lat, POINT_EXACT_M, 5)
    if covariate.fields:
        stage_1["PROPERTYNAME"] = ",".join(covariate.fields)
    hits = fetch(covariate, stage_1)
    if hits:
        return _field_values(covariate, hits[0])

    frame = LocalFrame(lon, lat)
    near = []
    for feature in fetch(covariate, _window_params(covariate, lon, lat, POINT_TOLERANCE_M, 20)):
        if feature.geometry is not None:
            near.append((frame.distance_m(feature.geometry), feature))
    near = [item for item in near if item[0] <= POINT_TOLERANCE_M]
    match = min(near, key=lambda item: item[0])[1] if near else None
    return _field_values(covariate, match)


def query_nearest(covariate: Covariate, lon: float, lat: float, fetch: Fetcher) -> dict:
    """Distance (km) to the nearest feature, plus its attributes (rivers, hydrographic network...).

    The search window grows until the best distance found fits inside it : any closer feature
    would then intersect the window too, so the result is exact.
    """
    frame = LocalFrame(lon, lat)
    best = None
    for radius in NEAREST_RADII_M:
        for feature in fetch(covariate, _window_params(covariate, lon, lat, radius, NEAREST_COUNT)):
            if feature.geometry is None:
                continue
            distance = frame.distance_m(feature.geometry)
            if best is None or distance < best[0]:
                best = (distance, feature)
        if best is not None and best[0] <= radius:
            break
    values = {covariate.target: None if best is None else round(best[0] / 1000.0, 4)}
    values.update(_field_values(covariate, None if best is None else best[1]))
    return values


def query_count(covariate: Covariate, lon: float, lat: float, fetch: Fetcher) -> dict:
    """Number of features within radius_m of the point (landslides, cavities...).

    Polygons are counted by their centroid : the layer holds events, not zones.
    """
    frame = LocalFrame(lon, lat)
    features = fetch(covariate, _window_params(covariate, lon, lat, covariate.radius_m, COUNT_MAX))
    if len(features) >= COUNT_MAX:
        warn(f"{covariate.layer_name} : {COUNT_MAX} entités reçues, le décompte est probablement tronqué")
    total = 0
    for feature in features:
        geometry = feature.geometry
        if geometry is None:
            continue
        if geometry.type() != QgsWkbTypes.PointGeometry:
            geometry = geometry.centroid()
        if frame.distance_m(geometry) <= covariate.radius_m:
            total += 1
    return {covariate.target: total}


STRATEGIES = {
    QUERY_POINT: query_point,
    QUERY_NEAREST: query_nearest,
    QUERY_COUNT: query_count,
}


def collect(covariate: Covariate, lon: float, lat: float, fetch: Optional[Fetcher] = None,
            feedback=None) -> dict:
    """Column name -> value for *covariate* at (lon, lat) ; None where there is no data.

    Raises WfsError if the server could not be queried (as opposed to "no data here").
    """
    fetch = fetch or default_fetcher(feedback)
    values = STRATEGIES[covariate.query_type](covariate, lon, lat, fetch)
    log(f"{covariate.target} ({lon:.5f}, {lat:.5f}) -> {values}")
    return values
