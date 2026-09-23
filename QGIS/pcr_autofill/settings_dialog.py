"""Configuration dialog : when filling happens, on which layers, with which covariates."""
from __future__ import annotations

from qgis.core import QgsProject, QgsVectorLayer, QgsWkbTypes
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from . import settings as cfg
from .covariates import DEFAULT_CONFIG, ConfigError, load_covariates

MODE_LABELS = (
    (cfg.MODE_OFF, "Désactivé (aucun remplissage à la création)"),
    (cfg.MODE_ASK, "Demander à chaque création d'élément"),
    (cfg.MODE_AUTO, "Automatique"),
)


class SettingsDialog(QDialog):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._settings = manager.settings.copy()
        self._covariates = manager.covariates
        self._config_error = manager.config_error
        self.setWindowTitle("PCR Autofill — configuration")
        self.setMinimumWidth(600)
        self._build()
        self._populate()

    # -- construction ---------------------------------------------------------

    def _build(self):
        layout = QVBoxLayout(self)

        trigger = QGroupBox("Déclenchement à la création d'un élément")
        form = QFormLayout(trigger)
        self.modeCombo = QComboBox()
        for mode, label in MODE_LABELS:
            self.modeCombo.addItem(label, mode)
        self.overwriteCheck = QCheckBox("Remplacer les valeurs déjà renseignées")
        self.statusCheck = QCheckBox(f"Tenir à jour le champ « {cfg.STATUS_FIELD} » (ok / partiel / erreur)")
        form.addRow("Mode", self.modeCombo)
        form.addRow(self.overwriteCheck)
        form.addRow(self.statusCheck)
        layout.addWidget(trigger)

        layers = QGroupBox("Couches surveillées (cochez celles à remplir)")
        box = QVBoxLayout(layers)
        self.layerList = QListWidget()
        self.layerList.setMaximumHeight(120)
        box.addWidget(self.layerList)
        self.createFieldsButton = QPushButton("Créer les champs manquants dans la couche sélectionnée…")
        self.createFieldsButton.clicked.connect(self._create_fields)
        box.addWidget(self.createFieldsButton)
        layout.addWidget(layers)

        covariates = QGroupBox("Covariables à interroger")
        box = QVBoxLayout(covariates)
        self.configError = QLabel()
        self.configError.setStyleSheet("color: #b00020;")
        self.configError.setWordWrap(True)
        box.addWidget(self.configError)
        self.covariateList = QListWidget()
        box.addWidget(self.covariateList)
        row = QHBoxLayout()
        for label, state in (("Tout cocher", Qt.Checked), ("Tout décocher", Qt.Unchecked)):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, s=state: self._check_all_covariates(s))
            row.addWidget(button)
        row.addStretch()
        box.addLayout(row)
        layout.addWidget(covariates)

        altitude = QGroupBox("Altitude — exception (pas une covariable)")
        box = QVBoxLayout(altitude)
        self.elevationCheck = QCheckBox("Inclure l'altitude (API REST d'altimétrie IGN, RGE ALTI)")
        self.elevationCheck.setToolTip(
            "Contrairement aux covariables ci-dessus, l'altitude n'est PAS une couche WFS et n'est "
            "pas pilotée par couches_selection.json : c'est un appel direct, câblé en dur dans "
            "elevation.py, à l'API d'altimétrie de l'IGN. Elle ne peut être qu'activée ou désactivée "
            "ici, pas configurée davantage.")
        box.addWidget(self.elevationCheck)
        layout.addWidget(altitude)

        source = QGroupBox("Fichier de configuration des covariables (couches_selection.json)")
        row = QHBoxLayout(source)
        self.pathEdit = QLineEdit()
        self.pathEdit.setPlaceholderText(f"Par défaut : {DEFAULT_CONFIG}")
        self.pathEdit.editingFinished.connect(self._reload_config)
        browse = QPushButton("Parcourir…")
        browse.clicked.connect(self._browse)
        default = QPushButton("Par défaut")
        default.clicked.connect(lambda: (self.pathEdit.clear(), self._reload_config()))
        for widget in (self.pathEdit, browse, default):
            row.addWidget(widget)
        layout.addWidget(source)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self._apply)
        layout.addWidget(buttons)

    def _populate(self):
        settings = self._settings
        self.modeCombo.setCurrentIndex(max(self.modeCombo.findData(settings.mode), 0))
        self.overwriteCheck.setChecked(settings.overwrite)
        self.statusCheck.setChecked(settings.status_field)
        self.elevationCheck.setChecked(settings.elevation)
        self.pathEdit.setText(settings.config_path)

        self.layerList.clear()
        layers = [layer for layer in QgsProject.instance().mapLayers().values()
                  if isinstance(layer, QgsVectorLayer) and layer.isSpatial()]
        for layer in sorted(layers, key=lambda layer: layer.name().lower()):
            item = QListWidgetItem(f"{layer.name()}  ({QgsWkbTypes.displayString(layer.wkbType())})")
            item.setData(Qt.UserRole, layer.id())
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if layer.id() in settings.layer_ids else Qt.Unchecked)
            self.layerList.addItem(item)
        self._fill_covariate_list(settings.covariates)

    def _fill_covariate_list(self, enabled):
        """*enabled* : covariable_cible names to check ; None = every usable covariate."""
        self.configError.setText(self._config_error or "")
        self.covariateList.clear()
        for covariate in self._covariates:
            item = QListWidgetItem(f"{covariate.target}  —  {covariate.title}")
            item.setData(Qt.UserRole, covariate.target)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            if covariate.usable:
                columns = ", ".join(column.name for column in covariate.columns)
                item.setToolTip(f"{covariate.usage}\nSource : {covariate.provider or '?'} "
                                f"({covariate.query_type})\nChamps : {columns}")
                checked = enabled is None or covariate.target in enabled
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            else:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                item.setCheckState(Qt.Unchecked)
                item.setToolTip("Ignorée : champs_cibles est vide dans le fichier de configuration."
                                + (f"\n{covariate.note}" if covariate.note else ""))
            self.covariateList.addItem(item)

    # -- actions ----------------------------------------------------------------

    def _check_all_covariates(self, state):
        for index in range(self.covariateList.count()):
            item = self.covariateList.item(index)
            if item.flags() & Qt.ItemIsEnabled:
                item.setCheckState(state)

    def _checked_covariates(self) -> list:
        wanted = {self.covariateList.item(index).data(Qt.UserRole)
                  for index in range(self.covariateList.count())
                  if self.covariateList.item(index).checkState() == Qt.Checked}
        return [covariate for covariate in self._covariates if covariate.target in wanted]

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Fichier de configuration", self.pathEdit.text() or DEFAULT_CONFIG, "JSON (*.json)")
        if path:
            self.pathEdit.setText(path)
            self._reload_config()

    def _reload_config(self):
        path = self.pathEdit.text().strip()
        try:
            covariates = load_covariates(path or None)
        except ConfigError as exc:
            QMessageBox.warning(self, "Configuration invalide", str(exc))
            self.pathEdit.setText(self._settings.config_path)
            return
        self._settings.config_path = path
        self._covariates, self._config_error = covariates, None
        current = {covariate.target for covariate in self._checked_covariates()}
        known = {covariate.target for covariate in self._covariates}
        self._fill_covariate_list(sorted(current & known) if current else None)

    def _create_fields(self):
        item = self.layerList.currentItem()
        layer = QgsProject.instance().mapLayer(item.data(Qt.UserRole)) if item is not None else None
        if not isinstance(layer, QgsVectorLayer):
            QMessageBox.information(self, "PCR Autofill", "Cliquez d'abord sur une couche de la liste.")
            return
        columns = self._manager.columns_for(self._checked_covariates(), self.statusCheck.isChecked(),
                                           self.elevationCheck.isChecked())
        missing = self._manager.missing_columns(layer, columns)
        if not missing:
            QMessageBox.information(self, "PCR Autofill", f"« {layer.name()} » contient déjà tous les champs.")
            return
        if self._manager.confirm(f"Créer {len(missing)} champ(s) dans « {layer.name()} » ?\n\n"
                                 + ", ".join(column.name for column in missing)):
            self._manager.ensure_fields(layer, columns)

    def settings(self) -> cfg.Settings:
        """The settings described by the dialog's current state."""
        settings = self._settings.copy()
        settings.mode = self.modeCombo.currentData()
        if settings.mode != cfg.MODE_OFF:
            settings.last_active_mode = settings.mode
        settings.overwrite = self.overwriteCheck.isChecked()
        settings.status_field = self.statusCheck.isChecked()
        settings.elevation = self.elevationCheck.isChecked()
        settings.config_path = self.pathEdit.text().strip()
        settings.layer_ids = [self.layerList.item(index).data(Qt.UserRole)
                              for index in range(self.layerList.count())
                              if self.layerList.item(index).checkState() == Qt.Checked]
        usable = {covariate.target for covariate in self._covariates if covariate.usable}
        checked = [covariate.target for covariate in self._checked_covariates()]
        settings.covariates = None if set(checked) == usable else checked
        return settings

    def _apply(self):
        self._manager.set_settings(self.settings())

    def accept(self):
        self._apply()
        super().accept()
