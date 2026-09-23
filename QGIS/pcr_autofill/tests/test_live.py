"""Tests against the real IGN / BRGM servers. Opt-in : set PCR_LIVE=1 (public APIs, needs internet)."""
import os
import unittest

from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsProject, QgsVectorLayer

from pcr_autofill import elevation
from pcr_autofill import settings as cfg
from pcr_autofill.autofill import AutofillManager
from pcr_autofill.collector import WfsError, collect
from pcr_autofill.covariates import load_covariates

from .qgis_env import wait
from .test_manager import FakeIface

LON, LAT = -1.5536, 47.2184   # Nantes city centre


@unittest.skipUnless(os.environ.get("PCR_LIVE"), "set PCR_LIVE=1 to query the real servers")
class LiveTest(unittest.TestCase):
    def test_every_usable_covariate_against_the_real_servers(self):
        report = {}
        for covariate in (c for c in load_covariates() if c.usable):
            try:
                report[covariate.target] = collect(covariate, LON, LAT)
            except WfsError as exc:
                report[covariate.target] = f"ERREUR SERVEUR : {exc}"
        for target, result in report.items():
            print(f"\n  {target:24s} {result}")
        # IGN answers are required ; BRGM is printed only (its database is regularly out of connections)
        distance = report["dist_cours_eau_km"]
        self.assertIsInstance(distance, dict, distance)
        self.assertLess(distance["dist_cours_eau_km"], 2.0, "the Loire and the Erdre are close")
        self.assertIsInstance(report["dist_hydrographie_km"], dict)
        self.assertEqual(report["corine_code_2018"]["corine_code_2018"], "111", "continuous urban fabric")

    def test_elevation_against_the_real_ign_api(self):
        # The exception : not one of the covariates above, see elevation.py.
        result = elevation.collect(LON, LAT)
        self.assertIsInstance(result["altitude_m"], float)
        self.assertLess(abs(result["altitude_m"] - 13), 10, "Nantes city centre is a few metres above sea level")

    def test_creating_a_point_fills_it_from_the_real_servers(self):
        QgsProject.instance().clear()
        self.addCleanup(QgsProject.instance().clear)
        layer = QgsVectorLayer("Point?crs=EPSG:4326&field=name:string", "sites", "memory")
        QgsProject.instance().addMapLayer(layer)
        iface = FakeIface()
        manager = AutofillManager(iface, cfg.Settings(mode=cfg.MODE_AUTO, layer_ids=[layer.id()]))
        self.addCleanup(manager.shutdown)
        manager.refresh_watches()
        self.assertTrue(manager.ensure_fields(layer))
        layer.startEditing()
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(LON, LAT)))
        layer.addFeature(feature)
        wait(lambda: not manager.busy, timeout=180)
        values = {f.name(): layer.getFeature(feature.id())[f.name()] for f in layer.fields()}
        print("\n  " + "\n  ".join(f"{k:34s} {v}" for k, v in values.items()))
        print("  message :", iface.bar.messages[-1])
        self.assertIn(values["pcr_statut"], ("ok", "partiel"))
        self.assertLess(values["dist_cours_eau_km"], 2.0)
        self.assertLess(abs(values["altitude_m"] - 13), 10)


if __name__ == "__main__":
    unittest.main()
