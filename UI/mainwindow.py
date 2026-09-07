# This Python file uses the following encoding: utf-8
import os
import re
import sys
import json
import threads
import requests
from pprint import PrettyPrinter

# ---- Qt Imports ---- #

from PySide6.QtWidgets import \
    QWidget, QApplication, QMainWindow, QCheckBox, \
    QComboBox, QFormLayout, QSplitter, QLineEdit, \
    QPushButton, QGridLayout, QDoubleSpinBox, QSizePolicy
from PySide6.QtGui import \
    QRegularExpressionValidator, QColor
from PySide6.QtCore import \
    Qt, QRegularExpression, Slot


# ----Local Imports ---- #

from terminal import Terminal

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
    "COUNT":        "10"
}
DELTA = 0.0001

class Logger(object):
    def __init__(self):
        self.callbacks = []
        self.printer = PrettyPrinter(depth=2)

    def register(self, callback):
        self.callbacks.append(callback)

    def write(self, text):
        for c in self.callbacks:
            c(text)

logger = Logger()

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

        # Create Widgets
        self.form = QFormLayout()
        self.setLayout(self.form)

        self.serversWidget = QComboBox(objectName="Serveur")
        self.serversWidget.addItems(self.data.keys())
        self.form.addRow("Serveur", self.serversWidget)
        self.serversRow = self.form.rowCount() - 1
        self.serversWidget.setCurrentIndex(self.serversWidget.count()-1)

        self.layerWidget = QComboBox(objectName="Couche")
        self.form.addRow("Couche", self.layerWidget)
        self.layerRow = self.form.rowCount() - 1

        self.projWidget = QComboBox(objectName="Projection")
        self.form.addRow("Projection", self.projWidget)
        self.projRow = self.form.rowCount() - 1

        self.reqWidget = QComboBox()
        self.reqWidget.addItems(["GetCapabilities", "GetFeature", "DescribeFeatureType"])
        self.form.addRow("Request", self.reqWidget)
        self.reqRow = self.form.rowCount() - 1

        self.coordWidget = QLineEdit("-1.56157, 47.21954")
        self.coordsValidator = QRegularExpressionValidator(QRegularExpression(
            utils.COORD_PATTERN
        ))
        self.coordWidget.setValidator(self.coordsValidator)
        self.form.addRow("Coordinates", self.coordWidget)
        self.coordRow = self.form.rowCount() - 1

        self.sendWidget = QPushButton("Send request")
        self.form.addWidget(self.sendWidget)
        self.sendRow = self.form.rowCount() - 1

        self.combos = [self.serversWidget, self.layerWidget, self.projWidget]

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
        self.spinner.start()
        self.spinner.setVisible(False)
        self.form.addWidget(self.spinner)
        self.spinner.setSizePolicy(
            QSizePolicy.Policy.Minimum,
            QSizePolicy.Policy.Minimum
        )

        self.layout().setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.layout().setSpacing(15)

        self.serversWidget.currentTextChanged.connect(self.onServerChanged)
        self.layerWidget.currentTextChanged.connect(self.onLayerChanged)
        self.projWidget.currentTextChanged.connect(self.onProjectionChanged)
        self.sendWidget.clicked.connect(self.onClicked)

        self.serversWidget.setCurrentIndex(0)
        self.options = {}

        self.optionsFormat = {
            "show_coordinates": (QCheckBox, False),
            "bbox_size": (QDoubleSpinBox, 0.0001)
        }

        self.dataFormat = {}
        self.gridLayout = QGridLayout()

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

    def sendRequest(self, node: threads.Node):
        lon, lat = self.coordWidget.text().replace(" ", "").split(",")
        lon = float(lon)
        lat = float(lat)
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
            params["SRSNAME"] = utils.splitProjection(self.currentProjection)
            params["REQUEST"] = self.reqWidget.currentText()

            bbox = (lat-DELTA, lon-DELTA, lat+DELTA, lon+DELTA)
            bbox = [str(b) for b in bbox]
            bbox = ','.join(bbox)
            params["BBOX"] = bbox+","+params["SRSNAME"]
            ret = utils._get(self.currentServer.get("url", ""), params)
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
            log(response)
        else:
            log(f"Response of type {type(response)} is not handled")



class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.build()

    def build(self):
        self.capaSelector = CapaSelector()
        self.terminal = Terminal(globals() | locals(),self)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.capaSelector)
        self.splitter.addWidget(self.terminal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setSizes(
            [self.capaSelector.minimumWidth(), self.terminal.maximumWidth()]
        )
        self.splitter.setStretchFactor(0,0)
        self.splitter.setStretchFactor(1,1)
        self.splitter.setHandleWidth(10)
        self.setCentralWidget(self.splitter)

        logger.register(self.terminal.textZone.append) 
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
