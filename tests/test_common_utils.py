"""Tests de common/utils.py (noyau utilisé par l'appli bureau et par service_discovery).

Aucun accès réseau. Depuis la racine du dépôt :

    python -m unittest discover -s tests -v

Le plugin QGIS a ses propres tests, à lancer avec le Python de QGIS : voir QGIS/pcr_autofill/README.md.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests

from common import utils


class FakeResponse:
    """Le strict nécessaire de requests.Response pour send_request()."""

    def __init__(self, status=200, content_type="application/json", body=b"{}", payload=None):
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self.content = body
        self.url = "http://test"
        self._payload = payload if payload is not None else {}

    def __bool__(self):                       # comme requests : vrai si le statut est < 400
        return self.status_code < 400

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._payload


class SplitProjectionTest(unittest.TestCase):
    def test_extracts_the_ogc_code_from_a_capabilities_urn(self):
        self.assertEqual(utils.splitProjection("urn:ogc:def:crs:EPSG::2154"), "EPSG::2154")

    def test_handles_ignf_codes(self):
        self.assertEqual(utils.splitProjection("IGNF:LAMB93"), "IGNF:LAMB93")

    def test_unknown_format_gives_an_empty_string(self):
        self.assertEqual(utils.splitProjection("n'importe quoi"), "")


class HaversineTest(unittest.TestCase):
    def test_one_degree_of_latitude_is_about_111_km(self):
        self.assertAlmostEqual(utils.haversine_km(0, 0, 0, 1), 111.19, delta=0.05)

    def test_distance_to_itself_is_zero(self):
        self.assertEqual(utils.haversine_km(-1.55, 47.21, -1.55, 47.21), 0)


def square(lon0, lat0, lon1, lat1, **props):
    ring = [[lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1], [lon0, lat0]]
    return {"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [ring]}}


class FindContainingFeatureTest(unittest.TestCase):
    def test_returns_only_the_polygons_that_contain_the_point(self):
        here = square(-1.56, 47.21, -1.55, 47.22, nom="ici")
        elsewhere = square(-1.50, 47.21, -1.49, 47.22, nom="ailleurs")
        found = utils.find_containing_feature([elsewhere, here], -1.555, 47.215)
        self.assertEqual([f["properties"]["nom"] for f in found], ["ici"])

    def test_nothing_contains_a_point_far_away(self):
        self.assertEqual(utils.find_containing_feature([square(-1.56, 47.21, -1.55, 47.22)], 2.3, 48.8), [])

    def test_features_without_geometry_are_ignored(self):
        self.assertEqual(utils.find_containing_feature([{"properties": {}}, {"geometry": {}}], 0, 0), [])


@mock.patch.object(utils.time, "sleep")
class SendRequestTest(unittest.TestCase):
    def test_returns_the_parsed_json(self, _sleep):
        with mock.patch.object(utils.requests, "get",
                               return_value=FakeResponse(payload={"features": [1]})) as get:
            self.assertEqual(utils.send_request("http://u", {"OUTPUTFORMAT": "application/json"}),
                             {"features": [1]})
        get.assert_called_once()

    def test_parses_xml_answers(self, _sleep):
        xml = FakeResponse(content_type="text/xml; charset=UTF-8", body=b"<a><b>1</b></a>")
        with mock.patch.object(utils.requests, "get", return_value=xml):
            self.assertEqual(utils.send_request("http://u", {}), {"a": {"b": "1"}})

    def test_a_server_refusing_json_is_asked_again_without_the_format_parameter(self, _sleep):
        refusal = FakeResponse(
            status=400, content_type="text/xml",
            body=b"<ExceptionText>'application/json' is not a permitted output format</ExceptionText>")
        gml = FakeResponse(content_type="text/xml", body=b"<wfs:FeatureCollection/>")
        params = {"OUTPUTFORMAT": "application/json", "TYPENAMES": "x"}
        sent, answers = [], iter([refusal, gml])

        def fake_get(url, params, timeout):
            sent.append(dict(params))   # copie : send_request modifie ce dictionnaire après l'appel
            return next(answers)

        with mock.patch.object(utils.requests, "get", side_effect=fake_get):
            result = utils.send_request("http://u", params)
        self.assertIn("wfs:FeatureCollection", result)
        self.assertIn("OUTPUTFORMAT", sent[0])
        self.assertNotIn("OUTPUTFORMAT", sent[1])
        self.assertIn("OUTPUTFORMAT", params, "le dictionnaire de l'appelant n'est pas modifié")

    def test_gives_none_after_all_attempts_fail(self, _sleep):
        with mock.patch.object(utils.requests, "get", side_effect=requests.ConnectionError("down")) as get:
            self.assertIsNone(utils.send_request("http://u", {}))
        self.assertEqual(get.call_count, utils.MAX_RETRIES)

    def test_succeeds_on_a_later_attempt(self, _sleep):
        good = FakeResponse(payload={"ok": True})
        with mock.patch.object(utils.requests, "get",
                               side_effect=[requests.Timeout("slow"), good]) as get:
            self.assertEqual(utils.send_request("http://u", {}), {"ok": True})
        self.assertEqual(get.call_count, 2)


class LoadJsonTest(unittest.TestCase):
    def test_reads_a_utf8_json_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "x.json")
            path.write_text(json.dumps({"vég": "été"}, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(utils.loadJson(str(path)), {"vég": "été"})


if __name__ == "__main__":
    unittest.main()
