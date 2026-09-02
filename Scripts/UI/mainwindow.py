# This Python file uses the following encoding: utf-8
import sys
import json
import threads
import os
import requests
from PySide6.QtWidgets import \
    QWidget, QApplication, QMainWindow, QCheckBox, \
    QComboBox, QFormLayout, QSplitter, QLineEdit, \
    QPushButton, QTextEdit, QGridLayout, \
    QDoubleSpinBox, QSizePolicy, QVBoxLayout
from PySide6.QtGui import \
    QRegularExpressionValidator, QFontDatabase, QFontMetricsF, \
    QColor, QKeyEvent
from PySide6.QtCore import \
    Qt, QRegularExpression, Slot, QObject, QEvent
from typing import assert_type

def subDirs(dir: str):
    sys.path.append(dir)
    for path in os.listdir(dir):
        if os.path.isdir(path):
            sys.path.append(path)
            subDirs(path)

def setRoot(dir: str):
    os.chdir(dir)
    subDirs(dir)

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

    def register(self, callback):
        self.callbacks.append(callback)

    def write(self, text):
        for c in self.callbacks:
            c(text)

logger = Logger()

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

        self.serversWidget = QComboBox()
        self.serversWidget.addItems(self.data.keys())
        self.form.addRow("Serveur", self.serversWidget)
        self.serversRow = self.form.count() - 1
        self.serversWidget.setCurrentIndex(self.serversWidget.count()-1)

        self.layerWidget = QComboBox()
        self.form.addRow("Couche", self.layerWidget)
        self.layerRow = self.form.count() - 1

        self.projWidget = QComboBox()
        self.form.addRow("Projection", self.projWidget)
        self.projRow = self.form.count() - 1

        self.reqWidget = QComboBox()
        self.reqWidget.addItems(["GetCapabilities", "GetFeature", "DescribeFeatureType"])
        self.form.addRow("Request", self.reqWidget)
        self.reqRow = self.form.count() - 1

        self.coordWidget = QLineEdit("-1.56157, 47.21954")
        self.coordsValidator = QRegularExpressionValidator(QRegularExpression(
            utils.COORD_PATTERN
        ))
        self.coordWidget.setValidator(self.coordsValidator)
        self.form.addRow("Coordinates", self.coordWidget)
        self.coordRow = self.form.count() - 1

        self.sendWidget = QPushButton("Send request")
        self.form.addWidget(self.sendWidget)
        self.sendRow = self.form.count() - 1

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

        self.onServerChanged(self.serversWidget.currentText())
        self.onLayerChanged(self.layerWidget.currentText())
        self.onProjectionChanged(self.projWidget.currentText())
        self.options = {}

        self.optionsFormat = {
            "show_coordinates": (QCheckBox, False),
            "bbox_size": (QDoubleSpinBox, 0.0001)
        }

        self.dataFormat = {
            ""
        }

        self.gridLayout = QGridLayout()

    def blockAll(self):
        self.serversWidget.blockSignals(True)
        self.layerWidget.blockSignals(True)
        self.projWidget.blockSignals(True)

    def unblockAll(self):
        self.serversWidget.blockSignals(False)
        self.layerWidget.blockSignals(False)
        self.projWidget.blockSignals(False)

    def onServerChanged(self, text):
        self.blockAll()
        print(f"Server changed : {text}")
        self.currentServerId = text
        self.currentServer = self.data.get(text, {})
        self.layers = self.currentServer.get("couches", {})
        self.layerWidget.clear()
        self.layerWidget.addItems(self.layers.keys())
        self.unblockAll()

    def onLayerChanged(self, text):
        self.blockAll()
        print(f"Layer changed : {text}")
        self.currentLayer = self.layers[text]
        self.projections = self.currentLayer.get("projections", [])
        self.projWidget.clear()
        self.projWidget.addItems(self.projections)
        self.unblockAll()

    def onProjectionChanged(self, text):
        self.currentProjection = text

    def sendRequest(self, node: threads.Node):
        lat, lon = self.coordWidget.text().replace(" ", "").split(",")
        lat = float(lat)
        lon = float(lon)
        # Traitement particulier pour la donnée d'altitude
        if self.currentServerId == "geopf":
            params={"lon": lat, "lat": lon, 
                "resource": "ign_rge_alti_wld",
                "zonly": "true"}
            url = self.currentServer["url"]
            print(url)
            ret = requests.get(url, params=params)
            self.handleResponse(ret)
        else:
            params = PARAMS
            params["TYPENAMES"] = self.currentLayer["nom"]
            params["SRSNAME"] = "EPSG:" + self.currentProjection.split("::")[1]
            params["REQUEST"] = self.reqWidget.currentText()

            bbox = (lat-DELTA, lon-DELTA, lat+DELTA, lon+DELTA)
            bbox = [str(b) for b in bbox]
            bbox = ','.join(bbox)
            params["BBOX"] = bbox+","+params["SRSNAME"]
            ret = utils._get(self.currentServer.get("url", ""), params)
            self.handleResponse(ret)

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
            print("Error: No response")
        elif type(response) is requests.Response:
            if response.ok:
                # Only for altitude so far
                data = response.json()
                alt = data.get("elevations", [None])[0]
                print(f"Altitude : {alt}")
            else:
                print(f"Response not ok: {response.reason}")
        elif type(response) is dict:
            print(response.items())
        else:
            print(f"Response of type {type(response)} is not handled")

class CommandHistory(QObject):
    def __init__(self):
        super().__init__()
        self.history = []
        self.current = 0

    def __len__(self):
        return len(self.history)

    def append(self, cmd: str):
        self.history.append(cmd)

    def reset(self):
        self.current = len(self)

    def up(self):
        self.current = max(self.current - 1, 0)
        if (len(self) > 0) and (self.current >= 0):
            return self.history[self.current]
        else:
            return ""

    def down(self):
        self.current = min(self.current + 1, len(self))
        if (len(self) > 0) and (self.current >= 0) and (self.current < len(self)):
            return self.history[self.current]
        else:
            return ""

class Terminal(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.history = CommandHistory()
        self.savedEntry = ""

        self.inputTerm = QLineEdit(placeholderText="custom commands (type help())")
        self.form = QVBoxLayout()
        self.setLayout(self.form)

        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        font.setPointSize(11)
        metrics = QFontMetricsF(font)
        self.textZone = QTextEdit(self)
        self.textZone.setFont(font)
        self.textZone.setTabStopDistance(4*metrics.horizontalAdvance(' '))
        self.textZone.setReadOnly(True)
        self.textZone.setAcceptRichText(True)
        self.textZone.setAcceptRichText(True)

        self.form.addWidget(self.textZone)
        self.form.addWidget(self.inputTerm)

        self.setupInput()

    def setupInput(self):
        self.inputTerm.returnPressed.connect(self.onInput)
        self.inputTerm.installEventFilter(self)

    def eventFilter(self, obj, event: QEvent):
        if event.type() == QEvent.Type.KeyPress:
            keyEvent = QKeyEvent(event)
            if keyEvent.key() == Qt.Key.Key_Up:
                # Save current command in progress
                if self.history.current == len(self.history):
                    self.savedEntry = self.inputTerm.text()
                text = self.history.up()
                self.inputTerm.setText(text)
                event.accept()
            elif keyEvent.key() == Qt.Key.Key_Down:
                text = self.history.down()
                if self.history.current == len(self.history):
                    self.inputTerm.setText(self.savedEntry)
                else:
                    self.inputTerm.setText(text)
                event.accept()

        return super().eventFilter(obj,event)

    def onInput(self):
        command = self.inputTerm.text()
        self.inputTerm.clear()
        try:
            exec(command)
        except Exception as e:
            print(f"Invalid command: {command}")
        self.history.append(command)
        self.history.reset()

class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.build()

    def build(self):
        self.capaSelector = CapaSelector()
        self.terminal = Terminal(self)
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
