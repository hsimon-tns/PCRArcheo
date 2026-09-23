import math
import os
import tempfile
import threading
import unittest
from unittest import mock

from qgis.core import (
    NULL, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsCoordinateTransformContext,
    QgsFeature, QgsField, QgsFields, QgsGeometry, QgsPointXY, QgsProject, QgsVectorFileWriter,
    QgsVectorLayer, QgsWkbTypes,
)
from qgis.gui import QgsMessageBar
from qgis.PyQt.QtCore import QMetaType
from qgis.PyQt.QtWidgets import QMainWindow, QPushButton

from pcr_autofill import settings as cfg
from pcr_autofill import tasks
from pcr_autofill.autofill import AutofillManager, is_empty
from pcr_autofill.collector import Feature, WfsError
from pcr_autofill.elevation import ElevationError

from .qgis_env import bbox_of, pump, wait

LON, LAT = -1.5536, 47.2184
M_LAT = 1 / 111_200.0


class FakeBar:
    def __init__(self):
        self.messages, self.levels, self.widgets = [], [], []

    def pushMessage(self, title, text, level=None, duration=0):
        self.messages.append(text)
        self.levels.append(level)

    def createMessage(self, title, text):
        return QgsMessageBar.createMessage(title, text)

    def pushWidget(self, widget, level=None, duration=0):
        self.widgets.append(widget)

    def popWidget(self, widget):
        if widget in self.widgets:
            self.widgets.remove(widget)


class FakeIface:
    def __init__(self):
        self.bar, self.window, self.active = FakeBar(), QMainWindow(), None

    def messageBar(self):
        return self.bar

    def mainWindow(self):
        return self.window

    def activeLayer(self):
        return self.active


class FakeServer:
    """A WFS that always has data around the queried point : polygon, line 300 m north, 3 points."""

    def __init__(self):
        self.calls = []

    def __call__(self, covariate, params):
        self.calls.append((covariate, params))
        centre = bbox_of(params).center()
        props = {name: f"{name}-value" for name in covariate.fields}
        if covariate.query_type == "intersection_point":
            x, y = centre.x(), centre.y()
            return [Feature(QgsGeometry.fromWkt(
                f"POLYGON(({x-.001} {y-.001},{x+.001} {y-.001},{x+.001} {y+.001},{x-.001} {y+.001},{x-.001} {y-.001}))"),
                props)]
        if covariate.query_type == "intersection_bbox":
            y = centre.y() + 300 * M_LAT
            return [Feature(QgsGeometry.fromWkt(f"LINESTRING({centre.x()-.05} {y},{centre.x()+.05} {y})"), props)]
        return [Feature(QgsGeometry.fromPointXY(QgsPointXY(centre.x() + i * 0.002, centre.y())), {})
                for i in range(3)]

    def targets(self):
        return {covariate.target for covariate, _ in self.calls}


class ManagerTestCase(unittest.TestCase):
    CRS = "EPSG:4326"

    def setUp(self):
        QgsProject.instance().clear()
        self.addCleanup(QgsProject.instance().clear)
        patcher = mock.patch.object(tasks, "REQUEST_DELAY", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.iface = FakeIface()
        self.server = FakeServer()
        self.layer = QgsVectorLayer(f"Point?crs={self.CRS}&field=name:string", "sites", "memory")
        QgsProject.instance().addMapLayer(self.layer)
        self.settings = cfg.Settings(mode=cfg.MODE_AUTO, layer_ids=[self.layer.id()])
        self.elevation_calls = []
        self.manager = AutofillManager(
            self.iface, self.settings, fetch=self.server,
            elevation_fetch=lambda lon, lat: (self.elevation_calls.append((lon, lat)), 12.3)[-1])
        self.addCleanup(self.manager.shutdown)
        self.manager.refresh_watches()

    def add_point(self, lon=LON, lat=LAT, **attributes):
        feature = QgsFeature(self.layer.fields())
        if lon is not None:
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
        for name, value in attributes.items():
            feature[name] = value
        self.assertTrue(self.layer.addFeature(feature))   # sets the (temporary) id on `feature`
        return feature.id()

    def settle(self):
        wait(lambda: not self.manager.busy)

    def attributes(self, fid):
        feature = self.layer.getFeature(fid)
        return {field.name(): feature[field.name()] for field in feature.fields()}

    def click(self, label):
        (widget,) = self.iface.bar.widgets
        (button,) = [b for b in widget.findChildren(QPushButton) if b.text() == label]
        button.click()


class AutoModeTest(ManagerTestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.manager.ensure_fields(self.layer))
        self.layer.startEditing()

    def test_new_feature_is_filled_in_the_edit_buffer(self):
        fid = self.add_point()
        self.settle()
        values = self.attributes(fid)
        self.assertAlmostEqual(values["dist_cours_eau_km"], 0.3, delta=0.01)
        self.assertEqual(values["dist_cours_eau_km__toponyme"], "toponyme-value")
        self.assertEqual(values["type_vegetation"], "nature-value")
        self.assertEqual(values["corine_code_2018"], "code_18-value")
        self.assertEqual(values["nb_mvt_terrain_5km"], 3)
        self.assertEqual(values["altitude_m"], 12.3, "the exception : not one of the covariates")
        self.assertEqual(values["pcr_statut"], "ok")
        self.assertTrue(self.layer.isEditable())
        self.assertEqual(self.layer.dataProvider().featureCount(), 0, "nothing committed behind the user's back")

    def test_the_covariates_of_a_feature_are_queried_concurrently(self):
        barrier = threading.Barrier(3, timeout=5)   # released only when 3 requests are in flight together

        def meet(covariate, params):
            barrier.wait()
            return self.server(covariate, params)
        self.manager._fetch = meet
        fid = self.add_point()
        self.settle()
        self.assertEqual(self.attributes(fid)["pcr_statut"], "ok", "6 requests, 2 rendezvous of 3")

    def test_unusable_covariates_are_not_queried(self):
        self.add_point()
        self.settle()
        self.assertNotIn("litho_substrat", self.server.targets())
        self.assertNotIn("type_sol_inra", self.server.targets())
        self.assertIn("alea_argile", self.server.targets())

    def test_one_undo_step_reverts_the_filled_values_only(self):
        fid = self.add_point()
        self.settle()
        self.layer.undoStack().undo()
        values = self.attributes(fid)
        self.assertTrue(is_empty(values["dist_cours_eau_km"]))
        self.assertTrue(is_empty(values["pcr_statut"]))
        self.assertTrue(self.layer.getFeature(fid).isValid(), "the feature itself is kept")

    def test_a_burst_of_new_features_makes_one_job(self):
        jobs = []
        original = self.manager.fill
        self.manager.fill = lambda layer, fids, overwrite=None: (jobs.append(list(fids)), original(layer, fids, overwrite))
        fids = [self.add_point(LON + i * 0.001) for i in range(3)]
        self.settle()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(sorted(jobs[0]), sorted(fids))
        for fid in fids:
            self.assertEqual(self.attributes(fid)["pcr_statut"], "ok")

    def test_existing_values_are_kept_by_default(self):
        fid = self.add_point(type_vegetation="saisi à la main")
        self.settle()
        values = self.attributes(fid)
        self.assertEqual(values["type_vegetation"], "saisi à la main")
        self.assertEqual(values["nb_mvt_terrain_5km"], 3)

    def test_existing_values_are_replaced_when_overwrite_is_on(self):
        self.settings.overwrite = True
        fid = self.add_point(type_vegetation="saisi à la main")
        self.settle()
        self.assertEqual(self.attributes(fid)["type_vegetation"], "nature-value")

    def test_failing_server_gives_partial_status_and_reports_the_error(self):
        def flaky(covariate, params):
            if covariate.target == "alea_argile":
                raise WfsError("BRGM en panne")
            return self.server(covariate, params)
        self.manager._fetch = flaky
        fid = self.add_point()
        self.settle()
        values = self.attributes(fid)
        self.assertEqual(values["pcr_statut"], "partiel")
        self.assertEqual(values["nb_mvt_terrain_5km"], 3, "the other covariates are still filled")
        self.assertTrue(is_empty(values["alea_argile"]))
        self.assertIn("BRGM en panne", self.iface.bar.messages[-1])

    def test_every_server_failing_gives_error_status(self):
        def down(covariate, params):
            raise WfsError("réseau coupé")
        def elevation_down(lon, lat):
            raise ElevationError("réseau coupé")
        self.manager._fetch = down
        self.manager._fetch_elevation = elevation_down
        fid = self.add_point()
        self.settle()
        self.assertEqual(self.attributes(fid)["pcr_statut"], "erreur")

    def test_feature_without_geometry_is_skipped_with_a_message(self):
        self.add_point(lon=None)
        self.settle()
        self.assertEqual(self.server.calls, [])
        self.assertIn("géométrie", self.iface.bar.messages[-1])

    def test_status_column_can_be_switched_off(self):
        self.settings.status_field = False
        self.layer.rollBack()
        self.layer.dataProvider().deleteAttributes([self.layer.fields().indexOf("pcr_statut")])
        self.layer.updateFields()
        self.layer.startEditing()
        fid = self.add_point()
        self.settle()
        self.assertNotIn("pcr_statut", self.attributes(fid))
        self.assertEqual(self.attributes(fid)["nb_mvt_terrain_5km"], 3)

    def test_cancelling_stops_the_job(self):
        gate = threading.Event()
        self.manager._fetch = lambda covariate, params: (gate.wait(10), self.server(covariate, params))[1]
        fid = self.add_point()
        wait(lambda: self.manager._running is not None)
        self.manager.cancel_all()
        gate.set()
        self.settle()
        self.assertTrue(is_empty(self.attributes(fid)["nb_mvt_terrain_5km"]))
        self.assertIn("annulé", self.iface.bar.messages[-1])


class ElevationExceptionTest(ManagerTestCase):
    """Altitude is not one of the covariates : its own setting, its own fetch hook (elevation.py)."""

    def setUp(self):
        super().setUp()
        self.assertTrue(self.manager.ensure_fields(self.layer))
        self.layer.startEditing()

    def test_disabling_it_skips_the_fetch_and_the_column_is_not_required(self):
        self.settings.elevation = False
        self.layer.rollBack()
        self.layer.dataProvider().deleteAttributes([self.layer.fields().indexOf("altitude_m")])
        self.layer.updateFields()
        self.layer.startEditing()
        fid = self.add_point()
        self.settle()
        self.assertEqual(self.elevation_calls, [])
        self.assertNotIn("altitude_m", self.attributes(fid))
        self.assertEqual(self.attributes(fid)["pcr_statut"], "ok", "covariates alone still fill fine")

    def test_elevation_only_still_works_with_no_covariate_selected(self):
        self.settings.covariates = []   # every covariate deselected in the dialog
        fid = self.add_point()
        self.settle()
        self.assertEqual(self.attributes(fid)["altitude_m"], 12.3)
        self.assertTrue(is_empty(self.attributes(fid)["dist_cours_eau_km"]))
        self.assertEqual(self.server.calls, [], "no covariate was queried")

    def test_a_failing_elevation_request_gives_partial_status_without_blocking_the_covariates(self):
        def failing(lon, lat):
            raise ElevationError("IGN indisponible")
        self.manager._fetch_elevation = failing
        fid = self.add_point()
        self.settle()
        values = self.attributes(fid)
        self.assertTrue(is_empty(values["altitude_m"]))
        self.assertEqual(values["nb_mvt_terrain_5km"], 3, "the covariates are still filled")
        self.assertEqual(values["pcr_statut"], "partiel")
        self.assertIn("IGN indisponible", self.iface.bar.messages[-1])

    def test_deactivating_every_covariate_and_altitude_gives_a_clear_message(self):
        self.settings.covariates = []
        self.settings.elevation = False
        self.add_point()
        pump(0.9)
        self.assertIn("Aucune covariable sélectionnée (ni l'altitude)", self.iface.bar.messages[-1])


def make_shapefile(field_names) -> QgsVectorLayer:
    """A real, disk-backed .shp with the given text fields (for DBF 10-char truncation tests)."""
    path = os.path.join(tempfile.mkdtemp(), "sites.shp")
    fields = QgsFields()
    for name in field_names:
        fields.append(QgsField(name, QMetaType.Type.QString))
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "ESRI Shapefile"
    writer = QgsVectorFileWriter.create(
        path, fields, QgsWkbTypes.Point, QgsCoordinateReferenceSystem("EPSG:4326"),
        QgsCoordinateTransformContext(), options)
    del writer   # flushes and closes ; keeping it open would lock the file
    return QgsVectorLayer(path, "sites", "ogr")


class ShapefileFieldNamesTest(ManagerTestCase):
    """A shapefile silently truncates/launders field names past 10 characters (DBF limit) : the
    plugin must notice the requested names don't exist afterwards, clean up the orphan renamed
    fields it just created, and say clearly why — not just report a bare 'nothing was created'."""

    def setUp(self):
        super().setUp()
        self.shp = make_shapefile(["name"])
        QgsProject.instance().addMapLayer(self.shp)
        self.addCleanup(QgsProject.instance().removeMapLayer, self.shp.id())

    def test_long_names_are_reported_precisely_and_cleaned_up(self):
        ok = self.manager.ensure_fields(self.shp)
        self.assertFalse(ok)
        self.assertEqual([f.name() for f in self.shp.fields()], ["name"], "no orphan renamed fields left")
        self.assertEqual(self.iface.bar.levels[-1], 2)   # Qgis.MessageLevel.Critical
        text = self.iface.bar.messages[-1]
        self.assertIn("dist_cours_eau_km", text)
        self.assertIn("10 caractères", text)
        self.assertIn("GeoPackage", text)

    def test_names_that_fit_are_created_normally(self):
        # altitude_m (10) and pcr_statut (10) both fit ; only covariates need renaming.
        self.manager.settings.covariates = []
        self.assertTrue(self.manager.ensure_fields(self.shp))
        self.assertIn("altitude_m", [f.name() for f in self.shp.fields()])
        self.assertIn("pcr_statut", [f.name() for f in self.shp.fields()])


class CommitRaceTest(ManagerTestCase):
    def test_layer_saved_before_the_answers_arrive_is_still_filled(self):
        self.assertTrue(self.manager.ensure_fields(self.layer))
        gate = threading.Event()
        self.manager._fetch = lambda covariate, params: (gate.wait(10), self.server(covariate, params))[1]
        self.layer.startEditing()
        self.add_point()
        wait(lambda: self.manager._running is not None)   # the task is in flight, blocked on the "network"
        self.assertTrue(self.layer.commitChanges(), "the user saves and stops editing meanwhile")
        gate.set()
        self.settle()
        self.assertFalse(self.layer.isEditable())
        (feature,) = list(self.layer.getFeatures())
        self.assertAlmostEqual(feature["dist_cours_eau_km"], 0.3, delta=0.01)
        self.assertEqual(feature["pcr_statut"], "ok")


class ProjectedLayerTest(ManagerTestCase):
    CRS = "EPSG:2154"

    def test_geometry_is_sent_to_the_server_as_wgs84(self):
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        to_l93 = QgsCoordinateTransform(wgs84, self.layer.crs(), QgsProject.instance())
        x, y = to_l93.transform(QgsPointXY(LON, LAT))
        self.assertTrue(self.manager.ensure_fields(self.layer))
        self.layer.startEditing()
        self.add_point(x, y)
        self.settle()
        centre = bbox_of(self.server.calls[0][1]).center()
        self.assertAlmostEqual(centre.x(), LON, places=4)
        self.assertAlmostEqual(centre.y(), LAT, places=4)


class PromptTest(ManagerTestCase):
    def test_ask_mode_fills_only_once_the_user_accepts(self):
        self.settings.mode = cfg.MODE_ASK
        self.assertTrue(self.manager.ensure_fields(self.layer))
        self.layer.startEditing()
        fid = self.add_point()
        pump(0.9)
        self.assertEqual(self.server.calls, [], "nothing is sent before the user agrees")
        self.assertEqual(len(self.iface.bar.widgets), 1)
        self.click("Remplir")
        self.assertEqual(self.iface.bar.widgets, [], "the prompt is dismissed")
        self.settle()
        self.assertEqual(self.attributes(fid)["nb_mvt_terrain_5km"], 3)

    def test_missing_columns_need_consent_even_in_auto_mode(self):
        self.layer.startEditing()
        fid = self.add_point()
        pump(0.9)
        self.assertEqual(self.server.calls, [])
        self.assertIn("champs à créer", self.iface.bar.widgets[0].text())
        self.click("Créer les champs et remplir")
        self.settle()
        values = self.attributes(fid)
        self.assertEqual(values["nb_mvt_terrain_5km"], 3)
        self.assertEqual(values["pcr_statut"], "ok")

    def test_off_mode_does_nothing_and_stops_watching(self):
        self.assertTrue(self.manager.ensure_fields(self.layer))
        self.settings.mode = cfg.MODE_OFF
        self.manager.refresh_watches()
        self.assertEqual(self.manager._watched, {})
        self.layer.startEditing()
        self.add_point()
        pump(0.9)
        self.assertEqual(self.server.calls, [])
        self.assertEqual(self.iface.bar.widgets, [])


class ManualFillTest(ManagerTestCase):
    def setUp(self):
        super().setUp()
        self.settings.mode = cfg.MODE_OFF     # the manual action works whatever the automatic mode
        self.manager.refresh_watches()
        features = []
        for i in range(3):
            feature = QgsFeature(self.layer.fields())
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(LON + i * 0.01, LAT)))
            features.append(feature)
        self.layer.dataProvider().addFeatures(features)
        self.iface.active = self.layer

    def test_fills_only_the_selected_features_and_starts_editing(self):
        ids = sorted(f.id() for f in self.layer.getFeatures())
        self.layer.selectByIds(ids[:2])
        self.manager.confirm = lambda text: True    # agree to create the missing columns
        self.manager.fill_selected()
        self.settle()
        self.assertTrue(self.layer.isEditable(), "left in edit mode for the user to review and save")
        statuses = [self.layer.getFeature(i)["pcr_statut"] for i in ids]
        self.assertEqual(statuses[:2], ["ok", "ok"])
        self.assertTrue(is_empty(statuses[2]))

    def test_declining_to_create_columns_does_nothing(self):
        self.layer.selectAll()
        self.manager.confirm = lambda text: False
        self.manager.fill_selected()
        pump(0.3)
        self.assertEqual(self.server.calls, [])
        self.assertEqual(self.layer.fields().indexOf("pcr_statut"), -1)

    def test_no_selection_gives_a_message(self):
        self.manager.fill_selected()
        self.assertIn("Aucun élément sélectionné", self.iface.bar.messages[-1])

    def test_no_active_vector_layer_gives_a_message(self):
        self.iface.active = None
        self.manager.fill_selected()
        self.assertIn("couche vecteur", self.iface.bar.messages[-1])


class WatchTest(ManagerTestCase):
    def test_watching_follows_the_settings(self):
        self.assertIn(self.layer.id(), self.manager._watched)
        other = QgsVectorLayer("Point?crs=EPSG:4326", "autre", "memory")
        QgsProject.instance().addMapLayer(other)
        self.assertNotIn(other.id(), self.manager._watched)
        self.settings.layer_ids.append(other.id())
        self.manager.refresh_watches()
        self.assertIn(other.id(), self.manager._watched)

    def test_removing_a_layer_stops_watching_it_without_error(self):
        QgsProject.instance().removeMapLayer(self.layer.id())
        self.assertEqual(self.manager._watched, {})

    def test_toggle_off_then_on_restores_the_previous_mode(self):
        settings = self.manager.settings.copy()
        settings.last_active_mode, settings.mode = cfg.MODE_AUTO, cfg.MODE_OFF
        self.manager.set_settings(settings, persist=False)
        self.assertEqual(self.manager._watched, {})
        settings = self.manager.settings.copy()
        settings.mode = settings.last_active_mode
        self.manager.set_settings(settings, persist=False)
        self.assertIn(self.layer.id(), self.manager._watched)


class NotifyTest(ManagerTestCase):
    def test_the_message_log_gets_the_real_severity_not_always_info(self):
        from qgis.core import QgsApplication, Qgis
        received = []
        connection = QgsApplication.messageLog().messageReceived.connect(
            lambda message, tag, level: received.append((tag, level)) if tag == "PCR Autofill" else None)
        try:
            self.manager.notify("un souci", Qgis.MessageLevel.Critical)
        finally:
            QgsApplication.messageLog().messageReceived.disconnect(connection)
        self.assertIn(("PCR Autofill", Qgis.MessageLevel.Critical), received)


class SettingsTest(unittest.TestCase):
    def setUp(self):
        QgsProject.instance().clear()
        self.addCleanup(QgsProject.instance().clear)

    def test_round_trip_through_the_project(self):
        settings = cfg.Settings(mode=cfg.MODE_AUTO, last_active_mode=cfg.MODE_AUTO, layer_ids=["a", "b"],
                                covariates=["x"], overwrite=True, status_field=False, config_path="c.json")
        cfg.save(settings)
        self.assertEqual(cfg.load(), settings)

    def test_defaults_when_the_project_has_no_settings(self):
        self.assertEqual(cfg.load(), cfg.Settings())

    def test_garbage_is_sanitised(self):
        settings = cfg.Settings.from_dict({"mode": "nonsense", "last_active_mode": "off", "layer_ids": None,
                                           "covariates": [1], "unknown": True})
        self.assertEqual((settings.mode, settings.last_active_mode), (cfg.MODE_ASK, cfg.MODE_ASK))
        self.assertEqual((settings.layer_ids, settings.covariates), ([], ["1"]))

    def test_corrupt_json_falls_back_to_defaults(self):
        QgsProject.instance().writeEntry(cfg.SCOPE, cfg.KEY, "{oops")
        self.assertEqual(cfg.load(), cfg.Settings())

    def test_is_empty(self):
        self.assertTrue(all(is_empty(v) for v in (None, NULL, "", "   ")))
        self.assertFalse(any(is_empty(v) for v in (0, 0.0, "x", False)))


if __name__ == "__main__":
    unittest.main()
