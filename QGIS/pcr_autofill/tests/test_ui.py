import json
import os
import tempfile
import unittest
from unittest import mock

from qgis.core import QgsProject, QgsVectorLayer
from qgis.gui import QgsMessageBar
from qgis.PyQt.QtCore import QCoreApplication, QEvent, Qt
from qgis.PyQt.QtWidgets import QMainWindow, QMessageBox, QPushButton, QToolBar

import pcr_autofill
from pcr_autofill import settings as cfg
from pcr_autofill import settings_dialog
from pcr_autofill.autofill import AutofillManager
from pcr_autofill.settings_dialog import SettingsDialog

from .qgis_env import pump


class FakeIface:
    def __init__(self):
        self.window = QMainWindow()
        self.bar = QgsMessageBar()
        self.menu = []

    def mainWindow(self):
        return self.window

    def messageBar(self):
        return self.bar

    def activeLayer(self):
        return None

    def addToolBar(self, name):
        return self.window.addToolBar(name)

    def addPluginToMenu(self, menu, action):
        self.menu.append((menu, action))

    def removePluginMenu(self, menu, action):
        self.menu.remove((menu, action))


class UiTestCase(unittest.TestCase):
    def setUp(self):
        QgsProject.instance().clear()
        self.addCleanup(QgsProject.instance().clear)
        self.sites = QgsVectorLayer("Point?crs=EPSG:4326&field=name:string", "sites", "memory")
        self.zones = QgsVectorLayer("Polygon?crs=EPSG:4326", "zones", "memory")
        self.table = QgsVectorLayer("None?field=a:int", "tableau", "memory")
        for layer in (self.sites, self.zones, self.table):
            QgsProject.instance().addMapLayer(layer)


class DialogTest(UiTestCase):
    def setUp(self):
        super().setUp()
        self.iface = FakeIface()
        self.manager = AutofillManager(self.iface, cfg.Settings())
        self.addCleanup(self.manager.shutdown)
        self.dialog = SettingsDialog(self.manager)

    def layer_item(self, name):
        for index in range(self.dialog.layerList.count()):
            if self.dialog.layerList.item(index).text().startswith(name):
                return self.dialog.layerList.item(index)

    def covariate_item(self, target):
        for index in range(self.dialog.covariateList.count()):
            item = self.dialog.covariateList.item(index)
            if item.data(Qt.UserRole) == target:
                return item

    def test_lists_only_spatial_layers(self):
        names = [self.dialog.layerList.item(i).text() for i in range(self.dialog.layerList.count())]
        self.assertEqual(len(names), 2)
        self.assertTrue(names[0].startswith("sites") and names[1].startswith("zones"))
        self.assertTrue(all("(" in name for name in names), "geometry type is shown")

    def test_defaults_check_every_usable_covariate_and_disable_the_others(self):
        self.assertEqual(self.covariate_item("alea_argile").checkState(), Qt.Checked)
        unusable = self.covariate_item("litho_substrat")
        self.assertEqual(unusable.checkState(), Qt.Unchecked)
        self.assertFalse(unusable.flags() & Qt.ItemIsEnabled)
        self.assertIn("champs_cibles", unusable.toolTip())

    def test_unchanged_dialog_reproduces_the_settings(self):
        settings = self.dialog.settings()
        self.assertEqual(settings, cfg.Settings())
        self.assertIsNone(settings.covariates, "all usable covariates checked = no explicit list")

    def test_choices_are_reflected_in_the_settings(self):
        self.dialog.modeCombo.setCurrentIndex(self.dialog.modeCombo.findData(cfg.MODE_AUTO))
        self.dialog.overwriteCheck.setChecked(True)
        self.dialog.statusCheck.setChecked(False)
        self.dialog.elevationCheck.setChecked(False)
        self.layer_item("sites").setCheckState(Qt.Checked)
        self.covariate_item("alea_argile").setCheckState(Qt.Unchecked)
        settings = self.dialog.settings()
        self.assertEqual(settings.mode, cfg.MODE_AUTO)
        self.assertEqual(settings.last_active_mode, cfg.MODE_AUTO)
        self.assertTrue(settings.overwrite)
        self.assertFalse(settings.status_field)
        self.assertFalse(settings.elevation)
        self.assertEqual(settings.layer_ids, [self.sites.id()])
        self.assertNotIn("alea_argile", settings.covariates)
        self.assertIn("type_vegetation", settings.covariates)

    def test_elevation_defaults_to_enabled_and_is_not_a_covariate(self):
        self.assertTrue(self.dialog.elevationCheck.isChecked())
        self.assertTrue(self.dialog.settings().elevation)
        targets = {self.dialog.covariateList.item(i).data(Qt.UserRole)
                  for i in range(self.dialog.covariateList.count())}
        self.assertNotIn("altitude", targets)
        self.assertNotIn("altitude_m", targets)

    def test_choosing_off_keeps_the_mode_the_toggle_will_restore(self):
        self.dialog.modeCombo.setCurrentIndex(self.dialog.modeCombo.findData(cfg.MODE_OFF))
        settings = self.dialog.settings()
        self.assertEqual((settings.mode, settings.last_active_mode), (cfg.MODE_OFF, cfg.MODE_ASK))

    def test_check_all_and_none_skip_unusable_covariates(self):
        self.dialog._check_all_covariates(Qt.Unchecked)
        self.assertEqual(self.dialog.settings().covariates, [])
        self.dialog._check_all_covariates(Qt.Checked)
        self.assertIsNone(self.dialog.settings().covariates)
        self.assertEqual(self.covariate_item("litho_substrat").checkState(), Qt.Unchecked)

    def test_accepting_applies_and_persists_the_settings(self):
        self.dialog.modeCombo.setCurrentIndex(self.dialog.modeCombo.findData(cfg.MODE_AUTO))
        self.layer_item("zones").setCheckState(Qt.Checked)
        self.dialog.accept()
        self.assertEqual(self.manager.settings.mode, cfg.MODE_AUTO)
        self.assertEqual(cfg.load().layer_ids, [self.zones.id()], "stored in the project")
        self.assertIn(self.zones.id(), self.manager._watched)

    def write_config(self, content):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        handle.write(json.dumps(content))
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_custom_config_file_replaces_the_covariate_list(self):
        path = self.write_config([{"couche_nom": "a:b", "url": "http://x", "covariable_cible": "seul",
                                   "champs_cibles": ["f"]}])
        self.dialog.pathEdit.setText(path)
        self.dialog._reload_config()
        self.assertEqual(self.dialog.covariateList.count(), 1)
        self.assertEqual(self.dialog.settings().config_path, path)
        self.dialog.accept()
        self.assertEqual([c.target for c in self.manager.active_covariates()], ["seul"])

    def test_invalid_config_file_is_rejected_and_the_previous_one_kept(self):
        path = self.write_config({"pas": "une liste"})
        self.dialog.pathEdit.setText(path)
        with mock.patch.object(QMessageBox, "warning") as warning:
            self.dialog._reload_config()
        warning.assert_called_once()
        self.assertEqual(self.dialog.pathEdit.text(), "")
        self.assertGreater(self.dialog.covariateList.count(), 1)

    def test_create_missing_fields_button(self):
        self.dialog.layerList.setCurrentItem(self.layer_item("sites"))
        self.manager.confirm = lambda text: True
        self.dialog._create_fields()
        self.assertGreaterEqual(self.sites.fields().indexOf("nb_mvt_terrain_5km"), 0)
        self.assertGreaterEqual(self.sites.fields().indexOf(cfg.STATUS_FIELD), 0)
        self.assertGreaterEqual(self.sites.fields().indexOf("altitude_m"), 0)
        with mock.patch.object(QMessageBox, "information") as info:
            self.dialog._create_fields()
        self.assertIn("déjà", info.call_args[0][2])

    def test_create_fields_without_a_selected_layer_explains_itself(self):
        with mock.patch.object(QMessageBox, "information") as info:
            self.dialog._create_fields()
        self.assertIn("Cliquez", info.call_args[0][2])

    def test_status_checkbox_drives_the_columns_to_create(self):
        self.dialog.layerList.setCurrentItem(self.layer_item("sites"))
        self.dialog.statusCheck.setChecked(False)
        self.manager.confirm = lambda text: True
        self.dialog._create_fields()
        self.assertEqual(self.sites.fields().indexOf(cfg.STATUS_FIELD), -1)

    def test_elevation_checkbox_drives_the_columns_to_create(self):
        self.dialog.layerList.setCurrentItem(self.layer_item("sites"))
        self.dialog.elevationCheck.setChecked(False)
        self.manager.confirm = lambda text: True
        self.dialog._create_fields()
        self.assertEqual(self.sites.fields().indexOf("altitude_m"), -1)
        self.assertGreaterEqual(self.sites.fields().indexOf(cfg.STATUS_FIELD), 0)


class DialogConfigErrorTest(UiTestCase):
    def test_a_broken_configuration_is_shown_not_raised(self):
        settings = cfg.Settings(config_path=os.path.join(tempfile.gettempdir(), "absent_pcr.json"))
        manager = AutofillManager(FakeIface(), settings)
        self.addCleanup(manager.shutdown)
        dialog = SettingsDialog(manager)
        self.assertIn("introuvable", dialog.configError.text())
        self.assertEqual(dialog.covariateList.count(), 0)


class RealMessageBarTest(UiTestCase):
    """The manager against a real QgsMessageBar (the manager tests use a recording fake)."""

    def test_notification_prompt_and_button_with_the_real_widget(self):
        iface = FakeIface()
        manager = AutofillManager(iface, cfg.Settings(mode=cfg.MODE_ASK, layer_ids=[self.sites.id()]),
                                  fetch=lambda covariate, params: [])
        self.addCleanup(manager.shutdown)

        manager.notify("bonjour")
        self.assertIn("bonjour", iface.bar.currentItem().text())

        iface.bar.clearWidgets()
        manager.handle_new_features(self.sites, [1, 2])          # columns are missing : prompt to create them
        prompt = iface.bar.currentItem()
        self.assertIn("2 nouveaux éléments", prompt.text())
        (button,) = prompt.findChildren(QPushButton)
        self.assertEqual(button.text(), "Créer les champs et remplir")
        button.click()
        self.assertGreaterEqual(self.sites.fields().indexOf("nb_mvt_terrain_5km"), 0, "columns created")
        self.assertIn("géométrie", iface.bar.currentItem().text(), "fids 1 and 2 do not exist : reported, no crash")


class PluginTest(UiTestCase):
    def setUp(self):
        super().setUp()
        self.iface = FakeIface()
        self.plugin = pcr_autofill.classFactory(self.iface)
        self.plugin.initGui()
        self.addCleanup(self.plugin.unload)

    def toolbars(self):
        return [t for t in self.iface.window.findChildren(QToolBar) if t.objectName() == "PCRAutofillToolbar"]

    def test_gui_is_built(self):
        (toolbar,) = self.toolbars()
        self.assertEqual(len(toolbar.actions()), 3)
        self.assertEqual(len(self.iface.menu), 3)
        self.assertTrue(self.plugin.toggle_action.isCheckable())
        self.assertTrue(all(not a.icon().isNull() for a in self.plugin.actions))

    def test_toggle_reflects_the_mode_and_survives_a_round_trip(self):
        self.assertTrue(self.plugin.toggle_action.isChecked(), "default mode is 'ask'")
        self.plugin._on_toggle(False)
        self.assertEqual(self.plugin.manager.settings.mode, cfg.MODE_OFF)
        self.assertFalse(self.plugin.toggle_action.isChecked())
        self.assertIn("désactivé", self.plugin.toggle_action.toolTip())
        self.plugin._on_toggle(True)
        self.assertEqual(self.plugin.manager.settings.mode, cfg.MODE_ASK)
        self.assertIn("sur confirmation", self.plugin.toggle_action.toolTip())

    def test_toggle_restores_automatic_mode_if_that_was_the_last_one(self):
        settings = self.plugin.manager.settings.copy()
        settings.mode = settings.last_active_mode = cfg.MODE_AUTO
        settings.layer_ids = [self.sites.id()]
        self.plugin.manager.set_settings(settings)
        self.plugin._on_toggle(False)
        self.assertNotIn(self.sites.id(), self.plugin.manager._watched, "off stops watching")
        self.plugin._on_toggle(True)
        self.assertEqual(self.plugin.manager.settings.mode, cfg.MODE_AUTO)
        self.assertIn(self.sites.id(), self.plugin.manager._watched)
        self.assertEqual(cfg.load().mode, cfg.MODE_AUTO, "persisted in the project")

    def test_settings_dialog_opens_on_the_manager(self):
        with mock.patch("pcr_autofill.plugin.SettingsDialog") as dialog:
            self.plugin.open_settings()
        dialog.assert_called_once_with(self.plugin.manager, self.iface.window)
        dialog.return_value.exec_.assert_called_once()

    def test_settings_follow_the_project_file(self):
        settings = self.plugin.manager.settings.copy()
        settings.mode, settings.layer_ids = cfg.MODE_AUTO, [self.sites.id()]
        self.plugin.manager.set_settings(settings)
        path = os.path.join(tempfile.mkdtemp(), "p.qgz")
        self.assertTrue(QgsProject.instance().write(path))
        QgsProject.instance().clear()                       # a new / other project : back to defaults
        self.assertEqual(self.plugin.manager.settings.mode, cfg.MODE_ASK)
        self.assertTrue(QgsProject.instance().read(path))   # reopening the file restores them
        self.assertEqual(self.plugin.manager.settings.mode, cfg.MODE_AUTO)
        self.assertEqual(len(self.plugin.manager.settings.layer_ids), 1)
        self.assertTrue(self.plugin.toggle_action.isChecked())

    def test_unload_removes_everything_and_is_repeatable(self):
        self.plugin.unload()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)   # QGIS' event loop does this
        self.assertEqual(self.toolbars(), [], "no leftover toolbar : matters with Plugin Reloader")
        self.assertEqual(self.iface.menu, [])
        self.assertIsNone(self.plugin.manager)
        self.plugin.unload()   # QGIS may call it again : must not raise
        pump(0.05)
        QgsProject.instance().clear()   # project signals are disconnected : no callback into a dead manager


if __name__ == "__main__":
    unittest.main()
