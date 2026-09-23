# This Python file uses the following encoding: utf-8
import os
import re
import sys
import json
import requests
from pprint import PrettyPrinter

# ---- Qt Imports ---- #

from PySide6.QtWidgets import \
    QWidget, QApplication, QMainWindow, QFrame, \
    QComboBox, QFormLayout, QSplitter, QLineEdit, \
    QPushButton, QGridLayout, QDoubleSpinBox, QSizePolicy, \
    QSpinBox, QLabel, QHBoxLayout, QAbstractSpinBox, QVBoxLayout
from PySide6.QtGui import QColor
from PySide6.QtCore import \
    Qt, QRegularExpression, Slot, Signal


# ----Local Imports ---- #

import threads
from terminal import Terminal
from mapviewer import MapViewer
from dictview import DictionaryTreeWidget

def subDirs(dir: str):
    sys.path.append(dir)
    for path in os.listdir(dir):
        if os.path.isdir(path):
            sys.path.append(path)
            subDirs(path)

def setRoot(dir: str):
    os.chdir(dir)
    subDirs(dir)

global PROJECT_PATH
if getattr(sys, "frozen", False):
    # PyInstaller extraction dir (bundled datas/ live here) instead of a
    # __file__-relative path, which points into the frozen bundle's own
    # internals rather than the original project layout.
    PROJECT_PATH = sys._MEIPASS
else:
    PROJECT_PATH = os.path.abspath(__file__+"/../..")
setRoot(PROJECT_PATH)

import common.utils as utils
from spinner import WaitingSpinner

CAPA_PATH = "data/capa_selection.json"
PARAMS = {
    "SERVICE":      "WFS",
    "VERSION":      "2.0.0",
    "REQUEST":      "GetFeature",
    "OUTPUTFORMAT": "application/json",
    #"COUNT":        "10"
}

class Logger(object):
    def __init__(self):
        self.callbacks = []
        self.printer = PrettyPrinter(depth=4)

    def register(self, callback):
        self.callbacks.append(callback)

    def setDepth(self, depth: int):
        self.printer = PrettyPrinter(depth=depth)

    def write(self, text):
        for c in self.callbacks:
            c(text)

logger = Logger()

def filterKeys(data, keys: list[str]):
    """Garde uniquement les cles listees (a n'importe quel niveau
    d'imbrication), en conservant le chemin dict/liste qui y mene."""
    if not keys:
        return data
    if isinstance(data, dict):
        result = {}
        for k, v in data.items():
            if k in keys:
                result[k] = v
            elif isinstance(v, (dict, list)):
                filtered = filterKeys(v, keys)
                if filtered:
                    result[k] = filtered
        return result
    if isinstance(data, list):
        filtered = [filterKeys(v, keys) for v in data if isinstance(v, (dict, list))]
        return [f for f in filtered if f]
    return data

DEBUG=True
def log(message, level=0):
    if DEBUG:
        t = type(message)
        if t is str:
            print(message)
        elif t is dict:
            print(logger.printer.pformat(message))
        elif hasattr(message, "__iter__"):
            # List and analogs
            if message:
                tt = type(message[0])
            else:
                tt = ""
            print(f"{t}[{tt}]:")
            for i in message:
                print(f"\t{i}")
        else:
            print(message)

class CapaSelector(QWidget):
    SPINNER_RADIUS = 16
    dataReceived = Signal(dict)
    def __init__(self, parent=None):
        super().__init__(parent)

        self.threads = threads.ThreadManager(self)

        # Load Data

        with open(CAPA_PATH, "r") as f:
            self.data = json.load(f)

        self.currentLayer = {}
        self.layers = {}
        self.currentServer = {}

        self.currentServerId = ""
        self.build()

    def build(self):
        self.setLayout(QVBoxLayout())
        self.buildForm()

        self.spinnerContainer = QWidget()
        self.spinnerContainer.setLayout(QVBoxLayout())
        self.spinnerContainer.layout().setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.spinner = WaitingSpinner(
                self,
                center_on_parent=False,
                roundness=100.0,
                fade=76.62,
                radius=16,
                lines=27,
                line_length=27,
                line_width=3,
                speed=0.7,
                color=QColor(230, 230, 230)
        )
        self.spinnerContainer.layout().addWidget(self.spinner)
        self.layout().addWidget(self.spinnerContainer)
        self.spinnerContainer.setFixedHeight(self.spinner.height())
        self.spinner.start()
        self.spinner.setVisible(False)
        self.spinner.setSizePolicy(
            QSizePolicy.Policy.Minimum,
            QSizePolicy.Policy.Minimum
        )

        self.layout().addWidget(self.map)

    def addForm(self, name: str, callback, items = []) -> QWidget:
        widget = QComboBox()
        widget.addItems(items)
        self.form.addRow(name, widget)
        if callback is not None:
            widget.currentTextChanged.connect(callback)
        return widget

    def buildForm(self):
        # Create Widgets
        self.form = QFormLayout()
        self.formWidget = QWidget()
        self.formWidget.setLayout(self.form)

        self.serversWidget = self.addForm("Server", self.onServerChanged, self.data.keys())
        self.layerWidget = self.addForm("Layer", self.onLayerChanged)
        self.layerRow = self.form.rowCount() - 1
        self.projWidget = self.addForm("Projection", self.onProjectionChanged)
        self.projRow = self.form.rowCount() - 1
        self.reqWidget = self.addForm("Request", None, ["GetFeature", "GetCapabilities", "DescribeFeatureType"])
        self.reqRow = self.form.rowCount() - 1

        self.coordsLayout = QHBoxLayout()

        self.latLabel = QLabel("Lat: ")
        self.latLabel.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        self.latwidget = QDoubleSpinBox(
            minimum=-90, maximum=90, decimals=4, singleStep=0.001, value=47.1955
        )
        self.lonLabel = QLabel("Lon: ")
        self.lonLabel.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        self.lonwidget = QDoubleSpinBox(
            minimum=-180, maximum=180, decimals=4, singleStep=0.001, value=-1.5620
        )
        self.bboxWidget = QDoubleSpinBox(
            minimum=0.0001, maximum=1.0, decimals=4, value=0.001,
            stepType=QAbstractSpinBox.StepType.AdaptiveDecimalStepType
        )
        self.coordsLayout.addWidget(self.lonLabel)
        self.coordsLayout.addWidget(self.lonwidget)
        self.coordsLayout.addWidget(self.latLabel)
        self.coordsLayout.addWidget(self.latwidget)

        self.form.addRow("Coordinates", self.coordsLayout)
        self.coordRow = self.form.rowCount() - 1

        self.form.addRow("Bounding box size", self.bboxWidget)
        self.bboxRow = self.form.rowCount() - 1

        self.sendWidget = QPushButton("Send request")
        self.form.addWidget(self.sendWidget)
        self.sendRow = self.form.rowCount() - 1

        self.map = MapViewer((self.latwidget.value(), self.lonwidget.value()))
        self.form.addWidget(self.map)
        self.latwidget.valueChanged.connect(self.onCoordsChanged)
        self.lonwidget.valueChanged.connect(self.onCoordsChanged)
        self.bboxWidget.valueChanged.connect(self.onCoordsChanged)
        self.map.mapMoved.connect(self.onMapMoved)

        self.combos = [self.serversWidget, self.layerWidget, self.projWidget]

        self.sendWidget.clicked.connect(self.onClicked)
        
        self.onCoordsChanged()
        self.layout().addWidget(self.formWidget)
        self.serversWidget.setCurrentIndex(0)
        self.onServerChanged(self.serversWidget.currentText())

    def blockAll(self):
        self.serversWidget.blockSignals(True)
        self.layerWidget.blockSignals(True)
        self.projWidget.blockSignals(True)

    def unblockAll(self):
        self.serversWidget.blockSignals(False)
        self.layerWidget.blockSignals(False)
        self.projWidget.blockSignals(False)
        self.updateVisibility()

    def updateVisibility(self):
        for w in self.combos:
            count = w.count()
            visible = (count > 0)
            row = self.form.getWidgetPosition(w)[0]
            self.form.setRowVisible(row, visible)

    def onServerChanged(self, text):
        self.blockAll()
        log(f"Server changed : {text}")
        self.currentServerId = text
        self.currentServer = self.data.get(text, {})
        self.layers = self.currentServer.get("layers", {})
        self.layerWidget.clear()
        self.layerWidget.addItems(self.layers.keys())
        visible = self.layerWidget.count() > 0
        self.form.setRowVisible(self.layerRow, visible)
        self.onLayerChanged(self.layerWidget.currentText())
        self.unblockAll()

    def onLayerChanged(self, text):
        self.blockAll()
        log(f"Layer changed : {text}")
        self.currentLayer = self.layers[text]
        self.projections = self.currentLayer.get("projections", [])
        self.projWidget.clear()
        self.projWidget.addItems(self.projections)
        visible = self.projWidget.count() > 0
        self.form.setRowVisible(self.projRow, visible)
        self.onProjectionChanged(self.projWidget.currentText())
        self.unblockAll()

    def onProjectionChanged(self, text):
        log(f"Projection changed : {text}")
        self.currentProjection = text

    def onCoordsChanged(self):
        lat = self.latwidget.value()
        lon = self.lonwidget.value()
        delta = self.bboxWidget.value()
        self.latwidget.setSingleStep(delta)
        self.lonwidget.setSingleStep(delta)
        self.map.centerAt(lat, lon)
        self.map.drawBBox((lat-delta, lon-delta, lat+delta, lon+delta))

    def onMapMoved(self, lat, lon):
        """Deplacement manuel de la carte (drag) : reporte le nouveau centre
        sur les spinboxes sans redeclencher onCoordsChanged (qui recentrerait
        la carte inutilement), puis redessine juste le rectangle a la suite."""
        self.latwidget.blockSignals(True)
        self.lonwidget.blockSignals(True)
        self.latwidget.setValue(lat)
        self.lonwidget.setValue(lon)
        self.latwidget.blockSignals(False)
        self.lonwidget.blockSignals(False)
        delta = self.bboxWidget.value()
        self.map.drawBBox((lat-delta, lon-delta, lat+delta, lon+delta))

    def sendRequest(self, node: threads.Node):
        lat = self.latwidget.text()
        lon = self.lonwidget.text()
        lat = float(lat.replace(",", "."))
        lon = float(lon.replace(",", "."))
        # Traitement particulier pour la donnée d'altitude
        if self.currentServerId == "geopf":
            params={"lon": lon, "lat": lat,
                "resource": "ign_rge_alti_wld",
                "zonly": "true"}
            url = self.currentServer["url"]
            log(url)
            ret = requests.get(url, params=params)
            node.finished.emit(ret)
        else:
            params = dict(PARAMS)
            params["TYPENAMES"] = self.currentLayer["name"]
            params["SRSNAME"] = utils.splitProjection(self.currentProjection).replace("::", ":")
            params["REQUEST"] = self.reqWidget.currentText()

            delta = self.bboxWidget.value()
            bbox = (lon-delta, lat-delta, lon+delta, lat+delta)
            bbox = ['{0:.5g}'.format(b) for b in bbox]
            bbox = ','.join(bbox)
            params["BBOX"] = bbox+","+params["SRSNAME"]
            print(params["BBOX"])
            ret = utils.send_request(self.currentServer.get("url", ""), params)
            node.finished.emit(ret)

    def onClicked(self):
        self.threads.run(
            self.sendRequest,
            onFinished=self.handleResponse
        )
        self.setEnabled(False)
        self.spinner.setVisible(True)

    @Slot(object)
    def handleResponse(self, response: object):
        self.setEnabled(True)
        self.spinner.setVisible(False)
        lat = self.latwidget.value()
        lon = self.lonwidget.value()
        if response is None:
            log("Error: No response")
        elif type(response) is requests.Response:
            if response.ok:
                # Only for altitude so far
                data = response.json()
                alt = data.get("elevations", [None])[0]
                log(f"Altitude : {alt}")
            else:
                log(f"Response not ok: {response.reason}")
        elif type(response) is dict:
            #self.printData(response)
            self.dataReceived.emit(response)
            # Edit map
            self.map.clearGeometry()
            feats = response.get("features", [])
            if feats:
                if feats[0].get('geometry', {}).get('type', '') == "Multipolygon":
                    feats = utils.find_containing_feature(feats, lon, lat)
            for f in feats:
                g = f.get("geometry", {})
                self.map.addGeometry(g, f.get("properties", {}).get(""))
        else:
            log(f"Response of type {type(response)} is not handled")

    def findNameInFeature(self, feature):
        lookup = ['graphie_du_toponyme', 'toponyme', 'nom_officiel', 'numero']

    def printData(self, data: dict):
        if 'wfs:WFS_Capabilities' in data.keys():
            # Print capabilities
            capa = data['wfs:WFS_Capabilities']
            for k, v in capa.items():
                k = k.replace("@", "")
                k = k.split(":")
                key = k[-1]
                kType = k[0]
                if kType == "xmlns":
                    log(f"{key} : {v}")
        else:
            log("Features data")
            log(data)

class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.build()

    def build(self):
        self.capaSelector = CapaSelector()
        context = {
            "window": self,
            "map": self.capaSelector.map
        }
        self.terminal = Terminal(globals() | locals() | context, self)
        self.dictView = DictionaryTreeWidget({})
        self.capaSelector.dataReceived.connect(self.dictView.load_dictionary)
        self.capaSelector.dataReceived.connect(lambda d: self.terminal.addContext("response", d))
        rightPanel = QSplitter(Qt.Orientation.Vertical)
        rightPanel.setHandleWidth(10)
        rightPanel.addWidget(self.dictView)
        rightPanel.addWidget(self.terminal)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.capaSelector)
        self.splitter.addWidget(rightPanel)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setSizes(
            [self.capaSelector.minimumWidth(), self.terminal.maximumWidth()]
        )
        self.splitter.setStretchFactor(0,0)
        self.splitter.setStretchFactor(1,1)
        self.splitter.setHandleWidth(10)
        self.setCentralWidget(self.splitter)

        logger.register(self.terminal.appendRaw)
        logger.register(lambda t, stdout=sys.stdout :stdout.write(t))
        sys.stdout = logger
        sys.stderr = logger

WINDOW_SIZE = (800,480)

if __name__ == "__main__":
    app = QApplication([])
    window = MainWindow()
    window.resize(*WINDOW_SIZE)
    window.setWindowTitle("API Géoplateforme")
    window.show()
    sys.exit(app.exec())
