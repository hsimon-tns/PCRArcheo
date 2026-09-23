"""
pcr_tools/qgis_demo.py
-----------------------
Script PyQGIS de démonstration.
À coller dans la console Python de QGIS (Menu > Extensions > Console Python).

Fonctionnement :
  - Clic sur la carte  → crée un point aux coordonnées cliquées
  - OU saisie manuelle → crée un point aux coordonnées entrées
  Dans les deux cas : appel automatique à env_collector → remplissage
  de la table attributaire avec les covariables environnementales.

Pré-requis :
  1. pcr_tools/ accessible depuis QGIS (voir ci-dessous)
  2. couches_selection.json dans le dossier du projet QGIS
  3. pip install requests pandas  (depuis OSGeo4W Shell sur Windows,
     ou terminal sur Linux/Mac avec le Python de QGIS)

Comment rendre pcr_tools accessible depuis QGIS :
    Dans la console Python QGIS, avant d'exécuter ce script :
    >>> import sys
    >>> sys.path.insert(0, "/chemin/vers/mon_projet")
"""

import sys
import json
import time
from pathlib import Path

# --- Imports QGIS ---
from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsField,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
)
from qgis.gui import QgsMapToolEmitPoint, QgsMessageBar
from qgis.PyQt.QtCore import QVariant, Qt
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QProgressBar,
    QDoubleSpinBox, QMessageBox, QApplication,
)
from qgis.PyQt.QtCore import QThread, pyqtSignal


# ---------------------------------------------------------------------------
# Chargement de la configuration
# ---------------------------------------------------------------------------

def _load_selection(selection_json: str) -> list[dict]:
    path = Path(selection_json)
    if not path.exists():
        raise FileNotFoundError(
            f"couches_selection.json introuvable : {path.absolute()}\n"
            "Placez-le dans le même dossier que votre projet QGIS."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _build_fields(layers: list[dict]) -> list[QgsField]:
    """
    Construit la liste des champs attributaires à partir de couches_selection.json.
    Convention identique à env_collector :
      covariable_cible          → valeur principale
      covariable_cible__champ   → attributs associés
    """
    fields = [
        QgsField("id",    QVariant.String),
        QgsField("lon",   QVariant.Double),
        QgsField("lat",   QVariant.Double),
        QgsField("statut", QVariant.String),  # "enrichi" ou "erreur"
    ]
    for layer in layers:
        cov = layer["covariable_cible"]
        champs = layer.get("champs_cibles", [])

        # Champ principal (distance ou valeur)
        if layer["type_requete"] == "intersection_bbox":
            fields.append(QgsField(cov, QVariant.Double))
        elif layer["type_requete"] == "count_radius":
            fields.append(QgsField(cov, QVariant.Int))
        else:
            fields.append(QgsField(cov, QVariant.String))

        # Champs attributs associés
        if len(champs) > 1:
            for c in champs:
                fields.append(QgsField(f"{cov}__{c}", QVariant.String))

    return fields


# ---------------------------------------------------------------------------
# Thread d'enrichissement (pour ne pas bloquer l'interface QGIS)
# ---------------------------------------------------------------------------

class EnrichThread(QThread):
    """Appelle env_collector dans un thread séparé."""
    finished = pyqtSignal(dict)   # émet le dict de covariables
    error    = pyqtSignal(str)    # émet le message d'erreur

    def __init__(self, lon: float, lat: float, selection_json: str):
        super().__init__()
        self.lon = lon
        self.lat = lat
        self.selection_json = selection_json

    def run(self):
        try:
            # Import ici pour éviter les conflits au chargement du script
            from pcr_tools.env_collector import collect_covariates
            import tempfile, os, pandas as pd

            # Créer un CSV temporaire avec le point
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".csv", delete=False, encoding="utf-8"
            ) as f:
                f.write("id,lon,lat\nPOINT_DEMO,{},{}\n".format(self.lon, self.lat))
                csv_path = f.name

            df = collect_covariates(csv_path, self.selection_json, verbose=False)
            os.unlink(csv_path)

            # Convertir la première ligne en dict
            if df.empty:
                self.error.emit("env_collector n'a retourné aucune donnée.")
                return
            row = df.iloc[0].to_dict()
            self.finished.emit(row)

        except Exception as e:
            self.error.emit(str(e))


# ---------------------------------------------------------------------------
# Couche vecteur persistante dans le projet
# ---------------------------------------------------------------------------

LAYER_NAME = "PCR — Sites enrichis"

def _get_or_create_layer(fields: list[QgsField]) -> QgsVectorLayer:
    """
    Retourne la couche de travail si elle existe déjà dans le projet,
    sinon la crée et l'ajoute.
    """
    for layer in QgsProject.instance().mapLayers().values():
        if layer.name() == LAYER_NAME:
            return layer

    # Création d'une couche mémoire Point en WGS84
    layer = QgsVectorLayer("Point?crs=EPSG:4326", LAYER_NAME, "memory")
    provider = layer.dataProvider()
    provider.addAttributes(fields)
    layer.updateFields()

    # Style : point rouge visible
    layer.renderer().symbol().setColor(
        __import__("qgis.PyQt.QtGui", fromlist=["QColor"]).QColor(220, 50, 50)
    )
    layer.renderer().symbol().setSize(4)

    QgsProject.instance().addMapLayer(layer)
    return layer


def _add_point_to_layer(
    layer: QgsVectorLayer,
    lon: float,
    lat: float,
    site_id: str,
    covars: dict,
    statut: str = "enrichi",
) -> None:
    """Ajoute un point avec ses attributs à la couche."""
    feat = QgsFeature(layer.fields())
    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))

    # Remplir les attributs
    feat["id"]     = site_id
    feat["lon"]    = lon
    feat["lat"]    = lat
    feat["statut"] = statut

    for field in layer.fields():
        name = field.name()
        if name in ("id", "lon", "lat", "statut"):
            continue
        val = covars.get(name)
        if val is not None:
            try:
                feat[name] = val
            except Exception:
                feat[name] = str(val)

    layer.dataProvider().addFeature(feat)
    layer.updateExtents()
    layer.triggerRepaint()


# ---------------------------------------------------------------------------
# Interface de saisie manuelle
# ---------------------------------------------------------------------------

class CoordDialog(QDialog):
    """Fenêtre de saisie de coordonnées GPS."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ajouter un site — coordonnées GPS")
        self.setMinimumWidth(320)
        self.result_lon = None
        self.result_lat = None

        layout = QVBoxLayout()

        # Longitude
        h1 = QHBoxLayout()
        h1.addWidget(QLabel("Longitude (WGS84) :"))
        self.lon_spin = QDoubleSpinBox()
        self.lon_spin.setRange(-180, 180)
        self.lon_spin.setDecimals(6)
        self.lon_spin.setValue(-1.490000)
        h1.addWidget(self.lon_spin)
        layout.addLayout(h1)

        # Latitude
        h2 = QHBoxLayout()
        h2.addWidget(QLabel("Latitude  (WGS84) :"))
        self.lat_spin = QDoubleSpinBox()
        self.lat_spin.setRange(-90, 90)
        self.lat_spin.setDecimals(6)
        self.lat_spin.setValue(47.280000)
        h2.addWidget(self.lat_spin)
        layout.addLayout(h2)

        # Note format Google Maps
        note = QLabel(
            "ℹ️  Depuis Google Maps (clic droit) :\n"
            "    copier lat, lon  →  inverser l'ordre ici"
        )
        note.setStyleSheet("color: #666; font-size: 10px;")
        layout.addWidget(note)

        # Boutons
        h3 = QHBoxLayout()
        btn_ok     = QPushButton("Créer le point")
        btn_cancel = QPushButton("Annuler")
        btn_ok.clicked.connect(self._accept)
        btn_cancel.clicked.connect(self.reject)
        h3.addWidget(btn_ok)
        h3.addWidget(btn_cancel)
        layout.addLayout(h3)

        self.setLayout(layout)

    def _accept(self):
        self.result_lon = self.lon_spin.value()
        self.result_lat = self.lat_spin.value()
        self.accept()


# ---------------------------------------------------------------------------
# Fenêtre principale de la démo
# ---------------------------------------------------------------------------

class PCRDemoDialog(QDialog):
    """
    Fenêtre principale de la démo PCR.
    Propose le choix entre clic carte et saisie manuelle.
    """

    def __init__(self, iface, selection_json: str, parent=None):
        super().__init__(parent)
        self.iface          = iface
        self.selection_json = selection_json
        self.layers_config  = _load_selection(selection_json)
        self.fields         = _build_fields(self.layers_config)
        self.layer          = _get_or_create_layer(self.fields)
        self.map_tool       = None
        self._thread        = None
        self._pending_lon   = None
        self._pending_lat   = None
        self._point_counter = 0

        self.setWindowTitle("PCR — Enrichissement automatique de points")
        self.setMinimumWidth(400)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout()

        # Titre
        titre = QLabel("<b>Démo : création et enrichissement de points archéologiques</b>")
        layout.addWidget(titre)

        info = QLabel(
            f"Couches configurées : <b>{len(self.layers_config)}</b><br>"
            f"Couche cible : <b>{LAYER_NAME}</b>"
        )
        layout.addWidget(info)

        # Bouton clic carte
        self.btn_click = QPushButton("📍  Cliquer sur la carte pour placer un point")
        self.btn_click.setStyleSheet("padding: 8px; font-size: 13px;")
        self.btn_click.clicked.connect(self._activate_map_click)
        layout.addWidget(self.btn_click)

        # Bouton saisie manuelle
        btn_manual = QPushButton("⌨️  Saisir les coordonnées manuellement")
        btn_manual.setStyleSheet("padding: 8px; font-size: 13px;")
        btn_manual.clicked.connect(self._open_coord_dialog)
        layout.addWidget(btn_manual)

        # Barre de progression
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # mode indéterminé
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # Statut
        self.status_label = QLabel("Prêt.")
        self.status_label.setStyleSheet("color: #444; font-style: italic;")
        layout.addWidget(self.status_label)

        # Bouton fermer
        btn_close = QPushButton("Fermer")
        btn_close.clicked.connect(self.close)
        layout.addWidget(btn_close)

        self.setLayout(layout)

    # --- Clic carte ---

    def _activate_map_click(self):
        """Active l'outil de clic sur la carte."""
        self.map_tool = QgsMapToolEmitPoint(self.iface.mapCanvas())
        self.map_tool.canvasClicked.connect(self._on_map_clicked)
        self.iface.mapCanvas().setMapTool(self.map_tool)
        self.btn_click.setText("📍  Cliquez sur la carte…  (Échap pour annuler)")
        self.status_label.setText("Cliquez sur la carte pour placer un point.")
        self.showMinimized()  # minimiser pour laisser la carte visible

    def _on_map_clicked(self, point, button):
        """Callback appelé quand l'utilisateur clique sur la carte."""
        if button != Qt.LeftButton:
            return

        # Reprojeter en WGS84 si nécessaire
        canvas_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        wgs84      = QgsCoordinateReferenceSystem("EPSG:4326")
        if canvas_crs != wgs84:
            transform = QgsCoordinateTransform(
                canvas_crs, wgs84, QgsProject.instance()
            )
            point = transform.transform(point)

        lon, lat = point.x(), point.y()

        # Restaurer l'outil précédent
        self.iface.mapCanvas().unsetMapTool(self.map_tool)
        self.btn_click.setText("📍  Cliquer sur la carte pour placer un point")
        self.showNormal()

        self._enrich_point(lon, lat)

    # --- Saisie manuelle ---

    def _open_coord_dialog(self):
        dlg = CoordDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            self._enrich_point(dlg.result_lon, dlg.result_lat)

    # --- Enrichissement ---

    def _enrich_point(self, lon: float, lat: float):
        """Lance l'enrichissement dans un thread séparé."""
        self._pending_lon = lon
        self._pending_lat = lat
        self._point_counter += 1
        site_id = f"SITE_{self._point_counter:03d}"

        self.status_label.setText(
            f"Enrichissement de {site_id} ({lon:.5f}, {lat:.5f})…"
        )
        self.progress.setVisible(True)
        self.btn_click.setEnabled(False)

        # Créer le point immédiatement (statut "en cours")
        _add_point_to_layer(self.layer, lon, lat, site_id, {}, statut="en cours")

        # Lancer le thread
        self._thread = EnrichThread(lon, lat, self.selection_json)
        self._thread.finished.connect(lambda covars: self._on_enriched(site_id, lon, lat, covars))
        self._thread.error.connect(lambda err: self._on_error(site_id, lon, lat, err))
        self._thread.start()

    def _on_enriched(self, site_id, lon, lat, covars):
        """Appelé quand l'enrichissement est terminé."""
        # Supprimer le point temporaire et le recréer avec les vraies données
        self.layer.dataProvider().deleteFeatures(
            [f.id() for f in self.layer.getFeatures()
             if f["id"] == site_id and f["statut"] == "en cours"]
        )
        _add_point_to_layer(self.layer, lon, lat, site_id, covars, statut="enrichi")

        nb = len(self.layers_config)
        self.status_label.setText(
            f"✓  {site_id} enrichi — {nb} covariables ajoutées."
        )
        self.progress.setVisible(False)
        self.btn_click.setEnabled(True)

        # Ouvrir la table attributaire pour montrer le résultat
        self.iface.showAttributeTable(self.layer)

    def _on_error(self, site_id, lon, lat, error_msg):
        """Appelé en cas d'erreur d'enrichissement."""
        _add_point_to_layer(self.layer, lon, lat, site_id, {}, statut="erreur")
        self.status_label.setText(f"✗  Erreur : {error_msg[:80]}")
        self.progress.setVisible(False)
        self.btn_click.setEnabled(True)
        QMessageBox.warning(self, "Erreur d'enrichissement", error_msg)


# ---------------------------------------------------------------------------
# Point d'entrée — à coller dans la console Python de QGIS
# ---------------------------------------------------------------------------

def run_demo(selection_json: str = None):
    """
    Lance la démo PCR dans QGIS.

    Paramètre
    ---------
    selection_json : str, optional
        Chemin vers couches_selection.json.
        Par défaut : cherche dans le dossier du projet QGIS courant.

    Exemple d'utilisation dans la console QGIS :
        >>> exec(open("/chemin/vers/pcr_tools/qgis_demo.py").read())
        >>> run_demo()

        # Ou avec un chemin explicite :
        >>> run_demo("/chemin/vers/couches_selection.json")

        # Ou avec des coordonnées sans interface :
        >>> run_demo_point(-1.490, 47.280)
    """
    global _demo_dialog  # Garder une référence pour éviter le garbage collection

    # Résolution du chemin vers couches_selection.json
    if selection_json is None:
        project_path = QgsProject.instance().homePath()
        selection_json = str(Path(project_path) / "couches_selection.json")

    try:
        dlg = PCRDemoDialog(iface, selection_json)
        _demo_dialog = dlg
        dlg.show()
    except FileNotFoundError as e:
        QMessageBox.critical(None, "Fichier manquant", str(e))
    except Exception as e:
        QMessageBox.critical(None, "Erreur", str(e))


def run_demo_point(lon: float, lat: float, selection_json: str = None):
    """
    Version sans interface : enrichit directement un point aux coordonnées données.
    Utile pour tester rapidement dans la console.

    Exemple :
        >>> run_demo_point(-1.490, 47.280)
    """
    if selection_json is None:
        project_path = QgsProject.instance().homePath()
        selection_json = str(Path(project_path) / "couches_selection.json")

    layers_config = _load_selection(selection_json)
    fields        = _build_fields(layers_config)
    layer         = _get_or_create_layer(fields)

    from pcr_tools.env_collector import collect_covariates
    import tempfile, os

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8"
    ) as f:
        f.write(f"id,lon,lat\nDEMO,-{abs(lon)},{lat}\n")
        csv_path = f.name

    print(f"Enrichissement de ({lon}, {lat})…")
    df = collect_covariates(csv_path, selection_json, verbose=True)
    os.unlink(csv_path)

    covars = df.iloc[0].to_dict() if not df.empty else {}
    _add_point_to_layer(layer, lon, lat, "DEMO", covars, statut="enrichi")
    iface.showAttributeTable(layer)
    print("✓ Point ajouté à la couche et table attributaire ouverte.")
    return df


# ---------------------------------------------------------------------------
# Instructions dans la console QGIS
# ---------------------------------------------------------------------------

INSTRUCTIONS = """
╔══════════════════════════════════════════════════════════════╗
║          PCR Tools — Démo enrichissement QGIS                ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  Pré-requis :                                                ║
║    1. pcr_tools/ accessible (sys.path.insert)                ║
║    2. couches_selection.json dans le dossier du projet       ║
║    3. pip install requests pandas (OSGeo4W Shell)            ║
║                                                              ║
║  Utilisation :                                               ║
║                                                              ║
║    # Avec interface (clic + saisie)                          ║
║    run_demo()                                                ║
║                                                              ║
║    # Sans interface (coordonnées directes)                   ║
║    run_demo_point(-1.490, 47.280)                            ║
║                                                              ║
║    # Chemin explicite vers couches_selection.json            ║
║    run_demo("/chemin/vers/couches_selection.json")           ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
"""

print(INSTRUCTIONS)
