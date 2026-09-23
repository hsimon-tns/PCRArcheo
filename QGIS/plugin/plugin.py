import os

from types import ModuleType

qgis = ModuleType()
qgis.__name__ = "qgis"

import qgis

from qgis.core import QgsMessageLog, Qgis
from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox, QToolBar

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(PLUGIN_DIR, "icon.png")
TAG = "Hello PCR"


def log(message, niveau=Qgis.Info):
    """
    Écrit dans Vue > Panneaux > Messages du journal, onglet « Hello PCR ».
    Contrairement à print(), ces messages survivent au démarrage :
    la console Python n'est pas ouverte quand les plugins se chargent.
    """
    QgsMessageLog.logMessage(message, TAG, niveau)


class HelloPlugin:

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self._construite = False
        log("__init__ appelé")

    def initGui(self):
        visible = self.iface.mainWindow().isVisible()
        log(f"initGui appelé — fenêtre principale visible : {visible}")

        if visible:
            log("QGIS déjà démarré → construction immédiate")
            self._construire()
        else:
            log("Démarrage en cours → construction différée")
            branche = False
            try:
                self.iface.initializationCompleted.connect(self._construire)
                log("branché sur initializationCompleted")
                branche = True
            except AttributeError:
                log("initializationCompleted indisponible", Qgis.Warning)
            # Filet de sécurité : si le signal ne se déclenche pas
            QTimer.singleShot(3000, self._construire)
            if not branche:
                log("repli sur QTimer 3 s")

    def _construire(self):
        if self._construite:
            return
        self._construite = True
        log("construction de l'interface")

        icon = QIcon(ICON_PATH) if os.path.exists(ICON_PATH) else QIcon()
        if icon.isNull():
            log(f"icône vide ou introuvable : {ICON_PATH}", Qgis.Warning)

        self.action = QAction(icon, "Hello PCR", self.iface.mainWindow())
        self.action.setObjectName("HelloPCRAction")
        self.action.triggered.connect(self.run)

        self.iface.addPluginToMenu("Hello PCR", self.action)
        self.iface.addToolBarIcon(self.action)

        self._verifier()

    def _verifier(self):
        fenetre = self.iface.mainWindow()
        trouve = False
        for barre in fenetre.findChildren(QToolBar):
            if self.action in barre.actions():
                trouve = True
                titre = barre.windowTitle() or barre.objectName()
                log(f"action dans « {titre} » — visible : {barre.isVisible()}")
                if not barre.isVisible():
                    log("barre masquée : clic droit sur une barre pour l'afficher",
                        Qgis.Warning)
        if not trouve:
            log("action absente de toute barre d'outils", Qgis.Critical)

    def unload(self):
        log("unload appelé")
        try:
            self.iface.initializationCompleted.disconnect(self._construire)
        except Exception:
            pass
        if self.action is not None:
            self.iface.removePluginMenu("Hello PCR", self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None
        self._construite = False

    def run(self):
        import sys
        infos = [
            f"QGIS       : {Qgis.QGIS_VERSION}",
            f"Python     : {sys.version.split()[0]}",
            f"Exécutable : {sys.executable}",
            "",
            f"Dossier    : {PLUGIN_DIR}",
            f"Icône      : {'trouvée' if os.path.exists(ICON_PATH) else 'INTROUVABLE'}",
        ]
        QMessageBox.information(
            self.iface.mainWindow(), "Hello PCR",
            "Le plugin fonctionne.\n\n" + "\n".join(infos),
        )