from qgmap import QGoogleMap
from qgmap.presets import tilesets, googlePin, customPin

from PySide6.QtWidgets import QWidget, QVBoxLayout

import numpy as np


class MapViewer(QGoogleMap):
    def __init__(self, coords: tuple, parent=None):
        super().__init__(parent)
        self.coords = coords
        self.waitUntilReady()
        self.centerAt(*coords)
        self.setZoom(13)

    def drawBBox(self, bbox: tuple, color: str = "#3388ff", fillOpacity: float = 0.3):
        """Dessine un rectangle Leaflet pour bbox = (minLat, minLon, maxLat, maxLon),
        en remplacant celui pose par un appel precedent. Le remplissage reprend
        la couleur des bords (fillColor par defaut = color en Leaflet)."""
        minLat, minLon, maxLat, maxLon = bbox
        self.runScript(
            "if (window.__bboxRect) { window.__bboxRect.remove(); }"
            f"window.__bboxRect = L.rectangle("
            f"[[{minLat},{minLon}],[{maxLat},{maxLon}]], "
            f"{{color: {color!r}, weight: 2, fillOpacity: {fillOpacity}}}).addTo(map);"
        )

    def clearBBox(self):
        self.runScript(
            "if (window.__bboxRect) { window.__bboxRect.remove(); window.__bboxRect = null; }"
        )

    def clearGeometry(self):
        self.runScript(
            "if (window.__layers) {window.__layers.clearLayers(); } "
            )

    def addGeometry(self, g, name=''):
        print("Add geometry")
        self.runScript(
            "if (!window.__layers) {window.__layers = L.layerGroup(); }"
            )
        gType = g.get('type', None)
        coords = g.get('coordinates', [])
        print(f"G type = {gType}")
        print(f"coords = {coords}")
        if gType == 'MultiPolygon':
            for elt in coords:
                for shape in elt:
                    arr = np.array(shape)
                    arr = np.flip(arr, axis=1)
                    shape = arr.tolist()
                    print(f'Array shape = {arr.shape}')
                    self.runScript(
                        "window.window.__layers.addLayer("
                        f"L.polygon({shape}, "
                        "{color: 'orange'})"
                        ").addTo(map);"
                    )
        elif gType == "LineString":
            arr = np.array(coords)
            arr = np.flip(arr, axis=1)
            shape = arr.tolist()
            self.runScript(
                "window.window.__layers.addLayer("
                f"L.polyline({shape}, "
                "{color: 'brown'})"
                ").addTo(map);"
            )
        elif gType == "MultiLineString":
            for elt in coords:
                arr = np.array(elt)
                arr = np.flip(arr, axis=1)
                shape = arr.tolist()
                self.runScript(
                    "window.window.__layers.addLayer("
                    f"L.polyline({shape}, "
                    "{color: 'brown'})"
                    ").addTo(map);"
                )
        elif gType == "Point":
            self.runScript(
                "window.window.__layers.addLayer("
                f"L.marker({coords[::-1]}, "
                "{color: 'red', " \
                f"name: \"{name}\""
                "})"
                ").addTo(map);"
            )