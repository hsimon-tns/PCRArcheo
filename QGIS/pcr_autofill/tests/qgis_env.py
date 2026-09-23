"""Headless QGIS bootstrap and helpers shared by the tests."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import QgsApplication, QgsGeometry, QgsRectangle
from qgis.PyQt.QtCore import QCoreApplication

_app = None


def init():
    global _app
    if _app is None:
        _app = QgsApplication([], True)
        _app.initQgis()
    return _app


def wait(condition, timeout=30.0):
    """Run the Qt event loop until *condition()* holds (tasks and queued signals need it)."""
    end = time.time() + timeout
    while not condition():
        QCoreApplication.processEvents()
        if time.time() > end:
            raise TimeoutError("condition not reached")
        time.sleep(0.005)


def bbox_of(params) -> QgsRectangle:
    """Rectangle (lon/lat) of a GetFeature BBOX parameter sent as minLat,minLon,maxLat,maxLon,urn."""
    lat0, lon0, lat1, lon1, _crs = params["BBOX"].split(",")
    return QgsRectangle(float(lon0), float(lat0), float(lon1), float(lat1))


class FakeServer:
    """Answers GetFeature like a WFS : the features of the requested layer that intersect BBOX."""

    def __init__(self, data):
        self.data = data          # layer name -> [Feature]
        self.calls = []

    def __call__(self, covariate, params):
        self.calls.append((covariate.layer_name, params))
        box = QgsGeometry.fromRect(bbox_of(params))
        found = [f for f in self.data.get(covariate.layer_name, [])
                 if f.geometry is None or f.geometry.intersects(box)]
        return found[:int(params["COUNT"])]


def pump(seconds):
    """Process events for a fixed time (to check that something does NOT happen)."""
    end = time.time() + seconds
    while time.time() < end:
        QCoreApplication.processEvents()
        time.sleep(0.005)
