from qgmap import QGoogleMap
from qgmap.presets import tilesets, googlePin, customPin

from PySide6.QtWidgets import QWidget, QVBoxLayout


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
