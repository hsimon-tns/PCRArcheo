"""QGIS plugin glue : toolbar, menu and the three actions."""
from __future__ import annotations

import os

from qgis.core import QgsApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from . import settings as cfg
from .autofill import AutofillManager
from .settings_dialog import SettingsDialog

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
MENU = "&PCR Autofill"
TOOLBAR = "PCR Autofill"

MODE_TEXT = {
    cfg.MODE_OFF: "désactivé",
    cfg.MODE_ASK: "sur confirmation",
    cfg.MODE_AUTO: "automatique",
}


def _theme_icon(name: str, fallback: QIcon) -> QIcon:
    icon = QgsApplication.getThemeIcon(f"/{name}")
    return fallback if icon.isNull() else icon


class PcrAutofillPlugin:

    def __init__(self, iface):
        self.iface = iface
        self.manager = None
        self.toolbar = None
        self.actions = []

    # -- QGIS lifecycle ----------------------------------------------------------

    def initGui(self):
        self.manager = AutofillManager(self.iface, cfg.load())
        self.manager.connect_project()
        self.manager.settingsChanged.connect(self._refresh_toggle)

        window = self.iface.mainWindow()
        plugin_icon = QIcon(os.path.join(PLUGIN_DIR, "icon.png"))

        self.toggle_action = QAction(plugin_icon, "Remplissage à la création", window)
        self.toggle_action.setCheckable(True)
        self.toggle_action.triggered.connect(self._on_toggle)

        self.fill_action = QAction(
            _theme_icon("mActionCalculateField.svg", plugin_icon),
            "Remplir les éléments sélectionnés", window)
        self.fill_action.setToolTip("PCR Autofill : remplir les attributs environnementaux des éléments sélectionnés")
        self.fill_action.triggered.connect(self.manager.fill_selected)

        self.settings_action = QAction(
            _theme_icon("mActionOptions.svg", plugin_icon), "Configuration…", window)
        self.settings_action.triggered.connect(self.open_settings)

        # Own toolbar : the shared "Plugins" toolbar is hidden by default in QGIS profiles.
        self.toolbar = self.iface.addToolBar(TOOLBAR)
        self.toolbar.setObjectName("PCRAutofillToolbar")
        self.actions = [self.toggle_action, self.fill_action, self.settings_action]
        for action in self.actions:
            self.toolbar.addAction(action)
            self.iface.addPluginToMenu(MENU, action)

        self.manager.refresh_watches()
        self._refresh_toggle()

    def unload(self):
        if self.manager is not None:
            self.manager.shutdown()
            self.manager = None
        for action in self.actions:
            self.iface.removePluginMenu(MENU, action)
        self.actions = []
        if self.toolbar is not None:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
            self.toolbar = None

    # -- actions -------------------------------------------------------------------

    def open_settings(self):
        SettingsDialog(self.manager, self.iface.mainWindow()).exec_()

    def _on_toggle(self, checked: bool):
        """Toolbar button : switch filling on (last active mode) or off, keeping the rest."""
        settings = self.manager.settings.copy()
        if checked:
            settings.mode = settings.last_active_mode
        else:
            if settings.mode != cfg.MODE_OFF:
                settings.last_active_mode = settings.mode
            settings.mode = cfg.MODE_OFF
        self.manager.set_settings(settings)

    def _refresh_toggle(self):
        mode = self.manager.settings.mode
        self.toggle_action.setChecked(mode != cfg.MODE_OFF)
        watched = len(self.manager.settings.layer_ids)
        self.toggle_action.setToolTip(
            f"PCR Autofill : remplissage à la création d'un élément — {MODE_TEXT[mode]}"
            f" ({watched} couche{'s' if watched > 1 else ''} surveillée{'s' if watched > 1 else ''})")
