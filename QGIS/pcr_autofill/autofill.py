"""Watches layers for new features and fills their attributes in the background.

Flow for a feature created by the user :

    featureAdded  ->  debounce (a paste / import creates many)  ->  handle_new_features
        mode "auto" : fill right away          mode "ask" : message bar with a [Remplir] button
    fill  ->  job queue (one QgsTask at a time)  ->  EnrichTask (worker thread, network)
        -> FeatureResult (main thread)  ->  written into the layer's edit buffer

Values go through the edit buffer : the user reviews them, can undo (Ctrl+Z) and saves with the rest
of the edits. If the layer was saved before the answers came back, the feature is found again by its
geometry (its id changed at commit) and written through a short edit session.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from functools import partial
from typing import Optional

from qgis.core import (
    NULL, Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsCsException,
    QgsFeatureRequest, QgsField, QgsGeometry, QgsProject, QgsVectorDataProvider, QgsVectorLayer,
)
from qgis.core.additions.edit import edit
from qgis.PyQt.QtCore import QObject, QTimer, QVariant, pyqtSignal, pyqtSlot
from qgis.PyQt.QtWidgets import QMessageBox, QPushButton

from . import settings as cfg
from .covariates import Column, ConfigError, load_covariates
from .elevation import ELEVATION_COLUMN
from .logger import log, warn
from .tasks import EnrichTask, FeatureResult

TITLE = "PCR Autofill"
BATCH_DELAY_MS = 400
_LEVEL = getattr(Qgis, "MessageLevel", Qgis)


def make_field(column: Column) -> QgsField:
    if Qgis.QGIS_VERSION_INT >= 33800:
        from qgis.PyQt.QtCore import QMetaType
        kinds = {"string": QMetaType.Type.QString, "double": QMetaType.Type.Double,
                 "int": QMetaType.Type.Int}
    else:
        kinds = {"string": QVariant.String, "double": QVariant.Double, "int": QVariant.Int}
    return QgsField(column.name, kinds[column.kind])


def is_empty(value) -> bool:
    """NULL, None or blank text."""
    if value is None:
        return True
    if isinstance(value, QVariant):
        return value.isNull()
    if isinstance(value, str):
        return not value.strip()
    return value == NULL


@dataclass
class Job:
    layer_id: str
    fids: list
    overwrite: bool
    locations: dict = field(default_factory=dict)   # fid -> WKT (layer CRS), to find it again after a commit
    stats: Counter = field(default_factory=Counter)
    errors: list = field(default_factory=list)


class AutofillManager(QObject):
    settingsChanged = pyqtSignal()

    def __init__(self, iface, settings: cfg.Settings, fetch=None, elevation_fetch=None, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.settings = settings
        self._fetch = fetch                 # test hook : replaces the network layer
        self._fetch_elevation = elevation_fetch   # test hook for the altitude exception (see elevation.py)
        self._covariates = []
        self._config_error = None
        self._watched = {}                  # layer id -> (layer, slot)
        self._batches = {}                  # layer id -> [fid] created since the last flush
        self._timers = {}
        self._queue = deque()
        self._running = None                # (task, job)
        self._project_connections = []
        self.reload_covariates()

    # -- configuration ------------------------------------------------------

    @property
    def covariates(self) -> list:
        return list(self._covariates)

    @property
    def config_error(self) -> Optional[str]:
        return self._config_error

    @property
    def busy(self) -> bool:
        return self._running is not None or bool(self._queue) or bool(self._batches)

    def reload_covariates(self) -> None:
        try:
            self._covariates = load_covariates(self.settings.config_path or None)
            self._config_error = None
        except ConfigError as exc:
            self._covariates, self._config_error = [], str(exc)
            warn(f"Configuration : {exc}")

    def active_covariates(self) -> list:
        wanted = self.settings.covariates
        return [c for c in self._covariates if c.usable and (wanted is None or c.target in wanted)]

    def columns_for(self, covariates, status_field: Optional[bool] = None,
                    elevation: Optional[bool] = None) -> list:
        """Columns written for *covariates*, + the altitude and status columns (per the settings
        unless overridden). Altitude is NOT one of *covariates* : see elevation.py."""
        columns = [column for covariate in covariates for column in covariate.columns]
        if self.settings.elevation if elevation is None else elevation:
            columns.append(Column(ELEVATION_COLUMN, "double"))
        if self.settings.status_field if status_field is None else status_field:
            columns.append(Column(cfg.STATUS_FIELD, "string"))
        return columns

    def set_settings(self, settings: cfg.Settings, persist: bool = True) -> None:
        config_changed = settings.config_path != self.settings.config_path
        self.settings = settings
        if config_changed:
            self.reload_covariates()
        if persist:
            cfg.save(settings)
        self.refresh_watches()
        self.settingsChanged.emit()

    def reload_from_project(self, *_) -> None:
        self.settings = cfg.load()
        self.reload_covariates()
        self.refresh_watches()
        self.settingsChanged.emit()

    def connect_project(self) -> None:
        project = QgsProject.instance()
        for signal, slot in ((project.readProject, self.reload_from_project),
                             (project.cleared, self.reload_from_project),
                             (project.layersAdded, self.refresh_watches)):
            signal.connect(slot)
            self._project_connections.append((signal, slot))

    def shutdown(self) -> None:
        for signal, slot in self._project_connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._project_connections = []
        self.cancel_all()
        for layer_id in list(self._watched):
            self._unwatch(layer_id)
        for timer in self._timers.values():
            timer.stop()
        self._batches.clear()

    # -- watching layers ----------------------------------------------------

    def refresh_watches(self, *_) -> None:
        """Watch exactly the configured layers (none while the mode is "off")."""
        wanted = set(self.settings.layer_ids) if self.settings.mode != cfg.MODE_OFF else set()
        for layer_id in list(self._watched):
            if layer_id not in wanted:
                self._unwatch(layer_id)
        for layer_id in wanted:
            layer = QgsProject.instance().mapLayer(layer_id)
            if layer_id not in self._watched and isinstance(layer, QgsVectorLayer):
                self._watch(layer)

    def _watch(self, layer: QgsVectorLayer) -> None:
        slot = partial(self._on_feature_added, layer.id())
        layer.featureAdded.connect(slot)
        layer.willBeDeleted.connect(partial(self._unwatch, layer.id()))
        self._watched[layer.id()] = (layer, slot)
        log(f"Surveillance de « {layer.name()} »")

    def _unwatch(self, layer_id: str) -> None:
        entry = self._watched.pop(layer_id, None)
        if entry is not None:
            layer, slot = entry
            try:
                layer.featureAdded.disconnect(slot)
            except (TypeError, RuntimeError):   # the layer is already being destroyed
                pass
        self._batches.pop(layer_id, None)

    def _on_feature_added(self, layer_id: str, fid: int) -> None:
        self._batches.setdefault(layer_id, []).append(fid)
        timer = self._timers.get(layer_id)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(BATCH_DELAY_MS)
            timer.timeout.connect(partial(self._flush, layer_id))
            self._timers[layer_id] = timer
        timer.start()   # restarts : the batch closes BATCH_DELAY_MS after the last creation

    def _flush(self, layer_id: str) -> None:
        fids = self._batches.pop(layer_id, [])
        layer = QgsProject.instance().mapLayer(layer_id)
        if fids and isinstance(layer, QgsVectorLayer):
            try:
                self.handle_new_features(layer, fids)
            except Exception as exc:
                warn(f"Traitement des nouveaux éléments : {exc}")

    # -- reacting to new features --------------------------------------------

    def handle_new_features(self, layer: QgsVectorLayer, fids: list) -> None:
        mode = self.settings.mode
        if mode == cfg.MODE_OFF:
            return
        covariates = self.active_covariates()
        if not covariates and not self.settings.elevation:
            self.notify("Aucune covariable sélectionnée (ni l'altitude) : rien à remplir.", _LEVEL.Warning)
            return
        missing = self.missing_columns(layer, self.columns_for(covariates))
        if mode == cfg.MODE_AUTO and not missing:
            self.fill(layer, fids)
        else:   # "ask", or "auto" that needs the user's consent before changing the layer's schema
            self._prompt(layer, fids, missing)

    def _prompt(self, layer: QgsVectorLayer, fids: list, missing: list) -> None:
        count = len(fids)
        noun = "nouvel élément" if count == 1 else "nouveaux éléments"
        text = f"{count} {noun} dans « {layer.name()} »."
        if missing:
            names = ", ".join(column.name for column in missing[:3]) + ("…" if len(missing) > 3 else "")
            text += f" {len(missing)} champ{'s' if len(missing) > 1 else ''} à créer ({names})."
            label = "Créer les champs et remplir"
        else:
            text += " Remplir les attributs environnementaux ?"
            label = "Remplir"
        bar = self.iface.messageBar()
        item = bar.createMessage(TITLE, text)
        button = QPushButton(label, item)
        button.clicked.connect(partial(self._accept_prompt, item, layer.id(), list(fids)))
        item.layout().addWidget(button)
        bar.pushWidget(item, _LEVEL.Info, 0)

    def _accept_prompt(self, item, layer_id: str, fids: list) -> None:
        self.iface.messageBar().popWidget(item)
        layer = QgsProject.instance().mapLayer(layer_id)
        if isinstance(layer, QgsVectorLayer) and self.ensure_fields(layer):
            self.fill(layer, fids)

    # -- manual action -------------------------------------------------------

    def fill_selected(self) -> None:
        layer = self.iface.activeLayer()
        if not isinstance(layer, QgsVectorLayer):
            self.notify("Sélectionnez d'abord une couche vecteur dans le panneau Couches.", _LEVEL.Warning)
            return
        fids = layer.selectedFeatureIds()
        if not fids:
            self.notify(f"Aucun élément sélectionné dans « {layer.name()} ».", _LEVEL.Warning)
            return
        covariates = self.active_covariates()
        if not covariates and not self.settings.elevation:
            self.notify("Aucune covariable sélectionnée (ni l'altitude) : ouvrez la configuration.",
                        _LEVEL.Warning)
            return
        missing = self.missing_columns(layer, self.columns_for(covariates))
        if missing and not self.confirm(
                f"{len(missing)} champ(s) sur {len(layer.fields())} manquent dans « {layer.name()} » :\n"
                + ", ".join(column.name for column in missing) + "\n\nLes créer ?"):
            return
        if not self.ensure_fields(layer):
            return
        if not layer.isEditable() and not layer.startEditing():
            self.notify(f"Impossible de passer « {layer.name()} » en édition.", _LEVEL.Critical)
            return
        self.fill(layer, fids)

    # -- layer schema ----------------------------------------------------------

    @staticmethod
    def missing_columns(layer: QgsVectorLayer, columns) -> list:
        fields = layer.fields()
        return [column for column in columns if fields.indexOf(column.name) < 0]

    def ensure_fields(self, layer: QgsVectorLayer, columns=None) -> bool:
        """Create the columns missing from *layer* (default : those of the active covariates)."""
        if columns is None:
            columns = self.columns_for(self.active_covariates())
        missing = self.missing_columns(layer, columns)
        if not missing:
            return True
        new_fields = [make_field(column) for column in missing]
        before = layer.fields().count()
        if layer.isEditable():
            # A list, not a generator : addAttribute() must run for every field regardless of
            # whether an earlier one failed (all() on a generator stops calling at the first False).
            created = all([layer.addAttribute(new_field) for new_field in new_fields])
        else:
            provider = layer.dataProvider()
            if not provider.capabilities() & QgsVectorDataProvider.AddAttributes:
                self.notify(f"La source de « {layer.name()} » n'accepte pas l'ajout de champs.",
                            _LEVEL.Critical)
                return False
            created = provider.addAttributes(new_fields)
            layer.updateFields()
        still_missing = [column.name for column in self.missing_columns(layer, columns)]
        if not still_missing:
            return True
        if layer.fields().count() > before:
            # Fields WERE added (created/provider.addAttributes() said so) but not under the exact
            # names asked for : the source silently renamed them (e.g. a shapefile/DBF truncates and
            # "launders" names past 10 characters). They're useless under the wrong name, and the
            # plugin will never find or write to them again ; remove them rather than leave orphans.
            self._remove_fields_from(layer, before)
            noun = "champ" if len(still_missing) == 1 else "champs"
            self.notify(
                f"« {layer.name()} » a renommé {len(still_missing)} {noun} au lieu de le(s) créer "
                f"tel(s) quel(s) (ex. « {still_missing[0]} »). La source limite probablement la longueur "
                "des noms de champs à 10 caractères (shapefile) : utilisez un GeoPackage.", _LEVEL.Critical)
        else:
            self.notify(f"Champs non créés : {', '.join(still_missing)}.", _LEVEL.Critical)
        return False

    @staticmethod
    def _remove_fields_from(layer: QgsVectorLayer, keep_count: int) -> None:
        """Delete every field beyond index *keep_count* (highest index first, indices shift on delete)."""
        extra = list(range(keep_count, layer.fields().count()))
        if not extra:
            return
        if layer.isEditable():
            for index in reversed(extra):
                layer.deleteAttribute(index)
        else:
            layer.dataProvider().deleteAttributes(extra)
            layer.updateFields()

    # -- job queue --------------------------------------------------------------

    def fill(self, layer: QgsVectorLayer, fids, overwrite: Optional[bool] = None) -> None:
        """Queue the enrichment of the given features (their fields must exist)."""
        self._queue.append(Job(
            layer.id(), list(fids), self.settings.overwrite if overwrite is None else overwrite))
        self._pump()

    def cancel_all(self) -> None:
        self._queue.clear()
        if self._running is not None:
            self._running[0].cancel()

    def _pump(self) -> None:
        if self._running is not None:
            return
        while self._queue:
            job = self._queue.popleft()
            layer = QgsProject.instance().mapLayer(job.layer_id)
            covariates = self.active_covariates()
            if not isinstance(layer, QgsVectorLayer) or (not covariates and not self.settings.elevation):
                continue
            items = self._locate(layer, job)
            if not items:
                self.notify("Aucun des éléments n'a de géométrie exploitable.", _LEVEL.Warning)
                continue
            task = EnrichTask(f"{TITLE} : {layer.name()} ({len(items)})", items, covariates, self._fetch,
                              include_elevation=self.settings.elevation,
                              elevation_fetch=self._fetch_elevation)
            task.featureDone.connect(self._on_feature_done)
            task.taskCompleted.connect(partial(self._on_task_end, True))
            task.taskTerminated.connect(partial(self._on_task_end, False))
            self._running = (task, job)   # keeps the task alive while it runs
            QgsApplication.taskManager().addTask(task)
            return

    @staticmethod
    def _locate(layer: QgsVectorLayer, job: Job) -> list:
        """(fid, lon, lat) of every usable feature ; runs on the main thread (reads the layer)."""
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        transform = None
        if layer.crs() != wgs84:
            transform = QgsCoordinateTransform(layer.crs(), wgs84, QgsProject.instance().transformContext())
        items = []
        for fid in job.fids:
            feature = layer.getFeature(fid)
            if not feature.isValid() or not feature.hasGeometry():
                continue
            anchor = feature.geometry().pointOnSurface()   # the point itself for point layers
            if anchor is None or anchor.isNull():
                continue
            point = anchor.asPoint()
            try:
                if transform is not None:
                    point = transform.transform(point)
            except QgsCsException:
                continue
            job.locations[fid] = feature.geometry().asWkt()
            items.append((fid, point.x(), point.y()))
        return items

    # -- results -----------------------------------------------------------------

    @pyqtSlot(object)
    def _on_feature_done(self, result: FeatureResult) -> None:
        if self._running is None:
            return
        job = self._running[1]
        layer = QgsProject.instance().mapLayer(job.layer_id)
        if not isinstance(layer, QgsVectorLayer):
            return
        try:
            self._apply(layer, job, result)
        except Exception as exc:
            warn(f"Écriture des valeurs (élément {result.fid}) : {exc}")

    def _apply(self, layer: QgsVectorLayer, job: Job, result: FeatureResult) -> None:
        fid = self._resolve_fid(layer, job, result.fid)
        if fid is None:
            job.stats["perdu"] += 1
            warn(f"Élément {result.fid} introuvable dans « {layer.name()} » (couche enregistrée ou modification annulée ?)")
            return
        if not result.errors:
            outcome = "ok"
        elif len(result.errors) >= result.queried:
            outcome = "erreur"
        else:
            outcome = "partiel"
        job.stats[outcome] += 1
        job.errors.extend(message for _, message in result.errors)

        values = dict(result.values)
        if self.settings.status_field:
            values[cfg.STATUS_FIELD] = outcome
        feature = layer.getFeature(fid)
        fields = layer.fields()
        changes = {}
        for name, value in values.items():
            index = fields.indexOf(name)
            if index < 0 or value is None:
                continue
            if name != cfg.STATUS_FIELD and not job.overwrite and not is_empty(feature[index]):
                continue
            changes[index] = value
        if changes:
            self._write(layer, fid, changes)

    @staticmethod
    def _resolve_fid(layer: QgsVectorLayer, job: Job, fid: int) -> Optional[int]:
        """The feature's current id : it changes when the layer is saved (temporary negative id)."""
        if layer.getFeature(fid).isValid():
            return fid
        wkt = job.locations.get(fid)
        if not wkt:
            return None
        geometry = QgsGeometry.fromWkt(wkt)
        box = geometry.boundingBox()
        margin = max(box.width(), box.height()) * 0.01 or 1e-7
        request = QgsFeatureRequest().setFilterRect(box.buffered(margin))
        for candidate in layer.getFeatures(request):
            if candidate.geometry().equals(geometry):
                return candidate.id()
        return None

    @staticmethod
    def _write(layer: QgsVectorLayer, fid: int, changes: dict) -> None:
        if layer.isEditable():
            layer.beginEditCommand(TITLE)   # one undo step per feature
            try:
                layer.changeAttributeValues(fid, changes)
            finally:
                layer.endEditCommand()
        else:   # saved before the answer arrived : short edit session, committed at once
            with edit(layer):
                layer.changeAttributeValues(fid, changes)

    def _on_task_end(self, completed: bool) -> None:
        running, self._running = self._running, None
        if running is not None:
            self._summarize(running[1], completed)
        self._pump()

    def _summarize(self, job: Job, completed: bool) -> None:
        layer = QgsProject.instance().mapLayer(job.layer_id)
        name = layer.name() if layer is not None else "?"
        done = sum(job.stats[key] for key in ("ok", "partiel", "erreur"))
        if not completed:
            self.notify(f"Remplissage annulé dans « {name} » ({done} élément(s) traité(s)).", _LEVEL.Warning)
            return
        detail = ", ".join(f"{count} {key}" for key, count in job.stats.items() if count)
        text = f"{done} élément(s) traité(s) dans « {name} » ({detail})."
        problems = sorted(set(job.errors))
        if problems:
            text += f" Erreur : {problems[0][:120]}" + (" (+ autres, voir le journal)" if len(problems) > 1 else "")
        elif isinstance(layer, QgsVectorLayer) and layer.isEditable():
            text += " Enregistrez la couche pour conserver les valeurs."
        self.notify(text, _LEVEL.Warning if problems else _LEVEL.Success)

    # -- user interaction -----------------------------------------------------------

    def notify(self, text: str, level=None, duration: int = 8) -> None:
        log(text, level)   # forward the level : otherwise every message logs as Info regardless
        self.iface.messageBar().pushMessage(TITLE, text, _LEVEL.Info if level is None else level, duration)

    def confirm(self, text: str) -> bool:
        answer = QMessageBox.question(self.iface.mainWindow(), TITLE, text)
        return answer == QMessageBox.Yes
