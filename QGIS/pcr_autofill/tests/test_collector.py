import json
import math
import unittest
from unittest import mock

from qgis.core import QgsGeometry

from pcr_autofill import collector
from pcr_autofill.collector import Feature, WfsError
from pcr_autofill.covariates import Covariate

from .qgis_env import FakeServer, bbox_of

LON, LAT = -1.5536, 47.2184
M_LAT = 1 / 111_200.0                                    # degrees of latitude per metre
M_LON = 1 / (111_320.0 * math.cos(math.radians(LAT)))    # degrees of longitude per metre


def geom(wkt):
    return QgsGeometry.fromWkt(wkt)


def point_at(east_m, north_m):
    return geom(f"POINT({LON + east_m * M_LON} {LAT + north_m * M_LAT})")


def square(half_m, east_m=0.0, north_m=0.0):
    x0, x1 = LON + (east_m - half_m) * M_LON, LON + (east_m + half_m) * M_LON
    y0, y1 = LAT + (north_m - half_m) * M_LAT, LAT + (north_m + half_m) * M_LAT
    return geom(f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))")


def cov(query_type, fields=(), radius=5000, layer="test:layer"):
    return Covariate(layer, layer, "TEST", "http://test", query_type, "cov", tuple(fields), radius, "", "")


class ParseFeaturesTest(unittest.TestCase):
    def test_geojson_3d_multipolygon_becomes_2d(self):
        body = json.dumps({"type": "FeatureCollection", "features": [{
            "type": "Feature", "properties": {"nature": "Bâti", "n": 3},
            "geometry": {"type": "MultiPolygon", "coordinates": [[[
                [-1.55, 47.21, 5.0], [-1.54, 47.21, 5.0], [-1.54, 47.22, 5.0], [-1.55, 47.21, 5.0]]]]}}]})
        (feature,) = collector.parse_features(body.encode())
        self.assertEqual(feature.properties["nature"], "Bâti")
        self.assertFalse(feature.geometry.constGet().is3D())
        self.assertAlmostEqual(feature.geometry.boundingBox().xMinimum(), -1.55)

    def test_gml_with_urn_axis_order_is_read_as_lon_lat(self):
        gml = """<?xml version="1.0" encoding="UTF-8"?>
        <wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:gml="http://www.opengis.net/gml/3.2"
            xmlns:ms="http://mapserver.gis.umn.edu/mapserver" numberMatched="1" numberReturned="1">
          <wfs:member><ms:MVT gml:id="MVT.1">
            <ms:msGeometry><gml:Point gml:id="p1" srsName="urn:ogc:def:crs:EPSG::4326">
              <gml:pos>47.2 -1.55</gml:pos></gml:Point></ms:msGeometry>
            <ms:type_mvt>Glissement</ms:type_mvt>
          </ms:MVT></wfs:member></wfs:FeatureCollection>"""
        (feature,) = collector.parse_features(gml.encode())
        point = feature.geometry.asPoint()
        self.assertAlmostEqual(point.x(), -1.55)   # lon
        self.assertAlmostEqual(point.y(), 47.2)    # lat
        self.assertEqual(feature.properties["type_mvt"], "Glissement")

    def test_parsing_from_several_threads_at_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from osgeo import ogr
        body = b'{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"a":1},'                b'"geometry":{"type":"Point","coordinates":[-1.5,47.2]}}]}'
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: collector.parse_features(body), range(64)))
        self.assertTrue(all(len(r) == 1 and r[0].properties["a"] == 1 for r in results))

    def test_unreadable_response_raises(self):
        with self.assertRaises(WfsError):
            collector.parse_features(b"<html>nope</html>")

    def test_no_memory_file_is_left_behind(self):
        from osgeo import gdal
        before = {entry for entry in (gdal.ReadDir("/vsimem/") or [])}
        collector.parse_features(b'{"type":"FeatureCollection","features":[]}')
        self.assertEqual({entry for entry in (gdal.ReadDir("/vsimem/") or [])}, before)


class GeometryTest(unittest.TestCase):
    def test_distance_to_a_point_is_geodesic_metres(self):
        distance = collector.LocalFrame(LON, LAT).distance_m(point_at(0, 1000))
        self.assertAlmostEqual(distance, 1000, delta=15)

    def test_distance_is_to_the_segment_not_to_the_nearest_vertex(self):
        line = geom(f"LINESTRING({LON - 0.05} {LAT + 1000 * M_LAT},{LON + 0.05} {LAT + 1000 * M_LAT})")
        distance = collector.LocalFrame(LON, LAT).distance_m(line)
        self.assertAlmostEqual(distance, 1000, delta=15)   # vertices are ~3.7 km away

    def test_distance_inside_a_polygon_is_zero(self):
        self.assertEqual(collector.LocalFrame(LON, LAT).distance_m(square(100)), 0)

    def test_window_uses_the_urn_form_with_lat_first_and_covers_the_radius(self):
        params = collector._window_params(cov("count_radius"), LON, LAT, 5000, 10)
        self.assertTrue(params["BBOX"].endswith(",urn:ogc:def:crs:EPSG::4326"))
        self.assertNotIn("PROPERTYNAME", params)
        box = bbox_of(params)
        self.assertAlmostEqual(box.yMinimum() + box.yMaximum(), 2 * LAT)   # lat is the first pair
        self.assertGreaterEqual(box.width() / M_LON, 2 * 5000 * 0.999)      # longitude widened by 1/cos(lat)
        self.assertGreaterEqual(box.height() / M_LAT, 2 * 5000 * 0.98)


class PointQueryTest(unittest.TestCase):
    def run_query(self, polygons, fields=("nature",)):
        server = FakeServer({"test:layer": polygons})
        return collector.query_point(cov("intersection_point", fields), LON, LAT, server)

    def test_returns_the_attributes_of_the_containing_polygon(self):
        result = self.run_query([
            Feature(square(500, east_m=2000), {"nature": "voisin"}),
            Feature(square(500), {"nature": "ici"})])
        self.assertEqual(result, {"cov": "ici"})

    def test_attribute_names_are_matched_case_insensitively(self):
        self.assertEqual(self.run_query([Feature(square(500), {"NATURE": "x"})]), {"cov": "x"})

    def test_point_a_few_metres_outside_a_border_still_matches(self):
        self.assertEqual(self.run_query([Feature(square(500, north_m=505), {"nature": "bord"})]), {"cov": "bord"})

    def test_polygon_further_than_the_tolerance_is_ignored(self):
        self.assertEqual(self.run_query([Feature(square(500, north_m=530), {"nature": "loin"})]), {"cov": None})

    def test_several_fields_give_one_suffixed_column_each(self):
        result = self.run_query([Feature(square(500), {"a": 1, "b": "deux"})], fields=("a", "b"))
        self.assertEqual(result, {"cov__a": "1", "cov__b": "deux"})

    def test_missing_attribute_is_none_not_an_error(self):
        self.assertEqual(self.run_query([Feature(square(500), {})]), {"cov": None})

    def test_stage_1_asks_the_server_for_attributes_only_in_a_metre_wide_window(self):
        server = FakeServer({"test:layer": [Feature(square(500), {"nature": "ici"})]})
        collector.query_point(cov("intersection_point", ("nature",)), LON, LAT, server)
        (_, params), = server.calls
        self.assertEqual(params["PROPERTYNAME"], "nature")
        box = bbox_of(params)
        self.assertLess(box.height() / M_LAT, 2 * 1.1)     # ~2 m tall : only the containing polygon matches

    def test_an_attributes_only_answer_is_enough(self):
        calls = []
        def attributes_only(covariate, params):
            calls.append(params)
            return [Feature(None, {"nature": "ici"})]     # what the server sends when PROPERTYNAME is given
        result = collector.query_point(cov("intersection_point", ("nature",)), LON, LAT, attributes_only)
        self.assertEqual(result, {"cov": "ici"})
        self.assertEqual(len(calls), 1, "no geometry download, no local test")

    def test_stage_2_only_runs_when_stage_1_found_nothing_and_skips_features_without_geometry(self):
        answers = iter([[], [Feature(None, {"nature": "x"})]])
        calls = []
        def fetch(covariate, params):
            calls.append(params)
            return next(answers)
        self.assertEqual(collector.query_point(cov("intersection_point", ("nature",)), LON, LAT, fetch),
                         {"cov": None})
        self.assertEqual(len(calls), 2)
        self.assertNotIn("PROPERTYNAME", calls[1], "stage 2 needs the geometry")

    def test_no_property_name_is_sent_when_the_config_lists_no_fields(self):
        server = FakeServer({"test:layer": [Feature(square(500), {})]})
        collector.query_point(cov("intersection_point", ()), LON, LAT, server)
        self.assertNotIn("PROPERTYNAME", server.calls[0][1])


class NearestQueryTest(unittest.TestCase):
    def run_query(self, features, fields=("nature",)):
        server = FakeServer({"test:layer": features})
        result = collector.query_nearest(cov("intersection_bbox", fields), LON, LAT, server)
        return result, server

    def test_distance_in_km_and_attributes_of_the_nearest_feature(self):
        line = geom(f"LINESTRING({LON - 0.05} {LAT + 300 * M_LAT},{LON + 0.05} {LAT + 300 * M_LAT})")
        result, server = self.run_query([Feature(line, {"nature": "Ruisseau"})])
        self.assertAlmostEqual(result["cov"], 0.3, delta=0.005)
        self.assertEqual(result["cov__nature"], "Ruisseau")
        self.assertEqual(len(server.calls), 1)   # found in the first, smallest window

    def test_search_window_grows_until_something_is_found(self):
        result, server = self.run_query([Feature(point_at(0, 3000), {"nature": "loin"})])
        self.assertAlmostEqual(result["cov"], 3.0, delta=0.03)
        self.assertEqual(len(server.calls), 3)   # 500 m, 2 km, then 5 km

    def test_keeps_searching_when_the_best_hit_may_not_be_the_nearest(self):
        # In the 2 km window's corner (2.7 km away) : a closer feature (2.3 km) may lie just outside it.
        result, server = self.run_query([
            Feature(point_at(1900, 1900), {"nature": "coin"}),
            Feature(point_at(0, 2300), {"nature": "plus proche"})])
        self.assertEqual(result["cov__nature"], "plus proche")
        self.assertAlmostEqual(result["cov"], 2.3, delta=0.03)

    def test_nothing_in_range_gives_none_everywhere(self):
        result, server = self.run_query([Feature(point_at(0, 50_000), {"nature": "x"})])
        self.assertEqual(result, {"cov": None, "cov__nature": None})
        self.assertEqual(len(server.calls), len(collector.NEAREST_RADII_M))


class CountQueryTest(unittest.TestCase):
    def count(self, features, radius=5000):
        server = FakeServer({"test:layer": features})
        return collector.query_count(cov("count_radius", radius=radius), LON, LAT, server)["cov"]

    def test_counts_only_features_inside_the_circle(self):
        features = [Feature(point_at(1000, 0), {}), Feature(point_at(4900, 0), {}),
                    Feature(point_at(5200, 0), {}),          # just outside, east
                    Feature(point_at(3900, 3900), {})]       # inside the search square, outside the circle
        self.assertEqual(self.count(features), 2)

    def test_east_west_reach_is_not_shortened_by_the_longitude_scale(self):
        # 4.9 km east is 0.065 degrees of longitude, more than the 0.045 a naive radius/111 km would fetch
        self.assertEqual(self.count([Feature(point_at(4900, 0), {})]), 1)

    def test_polygons_are_counted_by_their_centroid(self):
        self.assertEqual(self.count([Feature(square(2000, east_m=4000), {})]), 1)   # centroid 4 km away
        self.assertEqual(self.count([Feature(square(2000, east_m=6000), {})]), 0)   # reaches 4 km, centroid 6 km

    def test_zero_when_nothing_is_around(self):
        self.assertEqual(self.count([]), 0)


class NetworkTest(unittest.TestCase):
    def setUp(self):
        for memory in (collector._JSON_REFUSED, collector._REJECTED_PROPERTIES):
            memory.clear()
            self.addCleanup(memory.clear)
        patcher = mock.patch.object(collector.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    GEOJSON = b'{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"a":1},' \
              b'"geometry":{"type":"Point","coordinates":[-1.5,47.2]}}]}'
    REFUSED = b'<?xml version="1.0"?><ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/1.1">' \
              b"<ows:Exception><ows:ExceptionText>'application/json' is not a permitted output format " \
              b"for layer 'X'</ows:ExceptionText></ows:Exception></ows:ExceptionReport>"

    def request_mock(self, *responses):
        return mock.patch.object(collector, "_request", side_effect=list(responses))

    def test_json_is_requested_first(self):
        with self.request_mock((0, self.GEOJSON, "")) as request:
            (feature,) = collector.get_features("http://u", {"TYPENAMES": "X"})
        self.assertEqual(request.call_args[0][1]["OUTPUTFORMAT"], "application/json")
        self.assertEqual(feature.properties["a"], 1)

    def test_server_refusing_json_is_queried_in_its_default_format_and_remembered(self):
        with self.request_mock((3, self.REFUSED, "err"), (0, self.GEOJSON, "")) as request:
            collector.get_features("http://u", {"TYPENAMES": "X"})
        self.assertNotIn("OUTPUTFORMAT", request.call_args_list[1][0][1])
        with self.request_mock((0, self.GEOJSON, "")) as request:
            collector.get_features("http://u", {"TYPENAMES": "X"})
        self.assertNotIn("OUTPUTFORMAT", request.call_args[0][1])   # no second useless JSON attempt

    def test_transient_failure_is_retried(self):
        with self.request_mock((1, b"", "timeout"), (0, self.GEOJSON, "")) as request:
            collector.get_features("http://u", {"TYPENAMES": "X"})
        self.assertEqual(request.call_count, 2)

    def test_gives_up_after_the_retries_with_the_server_message(self):
        with self.request_mock(*[(3, self.REFUSED.replace(b"'application/json' is not a permitted output "
                                                          b"format for layer 'X'", b"DB down"), "x")] * 3):
            with self.assertRaisesRegex(WfsError, "DB down"):
                collector.get_features("http://u", {"TYPENAMES": "Y"})

    GEOSERVER_MSG = b"Requested property: foo is not available for X. The possible propertyName values are: [a, b]"
    MAPSERVER_MSG = b"msWFSGetFeature(): WFS server error. Invalid PROPERTYNAME foo"

    def exception(self, message):
        return self.REFUSED.replace(b"'application/json' is not a permitted output format for layer 'X'", message)

    def test_only_the_rejected_attribute_is_dropped_and_the_request_repeated(self):
        for message in (self.GEOSERVER_MSG, self.MAPSERVER_MSG):
            collector._REJECTED_PROPERTIES.clear()
            with self.request_mock((3, self.exception(message), "err"), (0, self.GEOJSON, "")) as request:
                collector.get_features("http://u", {"TYPENAMES": "P", "PROPERTYNAME": "a,foo,b"})
            self.assertEqual(request.call_args_list[0][0][1]["PROPERTYNAME"], "a,foo,b")
            self.assertEqual(request.call_args_list[1][0][1]["PROPERTYNAME"], "a,b", message)
            self.assertEqual(request.call_count, 2, "an adaptation is not a failure : no delay, no retry spent")

    def test_a_rejected_attribute_is_remembered_for_the_layer(self):
        with self.request_mock((3, self.exception(self.GEOSERVER_MSG), "err"), (0, self.GEOJSON, "")):
            collector.get_features("http://u", {"TYPENAMES": "P", "PROPERTYNAME": "a,foo"})
        with self.request_mock((0, self.GEOJSON, "")) as request:
            collector.get_features("http://u", {"TYPENAMES": "P", "PROPERTYNAME": "foo,a"})
        self.assertEqual(request.call_count, 1, "no new failed request for the same mistake")
        self.assertEqual(request.call_args[0][1]["PROPERTYNAME"], "a")
        with self.request_mock((0, self.GEOJSON, "")) as request:
            collector.get_features("http://u", {"TYPENAMES": "OTHER", "PROPERTYNAME": "foo"})
        self.assertEqual(request.call_args[0][1]["PROPERTYNAME"], "foo", "memory is per layer")

    def test_when_every_requested_attribute_is_unknown_it_fails_fast_with_an_actionable_message(self):
        one = self.exception(self.GEOSERVER_MSG)                       # names foo
        two = self.exception(self.GEOSERVER_MSG.replace(b"foo", b"bar"))
        with mock.patch.object(collector, "warn") as warning, self.request_mock((3, one, ""), (3, two, "")) as request:
            with self.assertRaisesRegex(WfsError, "aucun des champs demandés n'existe dans Z : foo, bar"):
                collector.get_features("http://u", {"TYPENAMES": "Z", "PROPERTYNAME": "foo,bar"})
        self.assertEqual(request.call_count, 2, "the whole feature is NOT downloaded for nothing")
        self.assertEqual(warning.call_count, 2, "each unknown attribute is reported once")
        with self.request_mock() as request:                             # next feature : no request at all
            with self.assertRaises(WfsError):
                collector.get_features("http://u", {"TYPENAMES": "Z", "PROPERTYNAME": "foo,bar"})
        request.assert_not_called()

    def test_unidentifiable_rejection_drops_every_attribute(self):
        with self.request_mock((3, self.exception(b"some property problem"), ""), (0, self.GEOJSON, "")) as request:
            collector.get_features("http://u", {"TYPENAMES": "P", "PROPERTYNAME": "a,b"})
        self.assertNotIn("PROPERTYNAME", request.call_args_list[1][0][1])

    def test_json_refusal_and_property_name_rejection_compose(self):
        rejected = self.REFUSED.replace(b"'application/json' is not a permitted output format for layer 'X'",
                                        b"Invalid PROPERTYNAME foo")
        with self.request_mock((3, self.REFUSED, ""), (3, rejected, ""), (0, self.GEOJSON, "")) as request:
            collector.get_features("http://u", {"TYPENAMES": "Q", "PROPERTYNAME": "foo,bar"})
        last = request.call_args_list[-1][0][1]
        self.assertNotIn("OUTPUTFORMAT", last)
        self.assertEqual(last["PROPERTYNAME"], "bar")

    def test_empty_answer_is_retried(self):
        with self.request_mock((0, b"", ""), (0, self.GEOJSON, "")) as request:
            (feature,) = collector.get_features("http://u", {"TYPENAMES": "E"})
        self.assertEqual(request.call_count, 2)
        self.assertEqual(feature.properties["a"], 1)

    def test_persistently_empty_answer_ends_in_an_error(self):
        with self.request_mock(*[(0, b"", "")] * 3):
            with self.assertRaisesRegex(WfsError, "illisible"):
                collector.get_features("http://u", {"TYPENAMES": "E2"})

    def test_a_request_timeout_is_set(self):
        request = mock.MagicMock()
        with mock.patch.object(collector, "QNetworkRequest", return_value=request), \
                mock.patch.object(collector, "QgsBlockingNetworkRequest") as blocking:
            blocking.return_value.reply.return_value.content.return_value = b""
            collector._request("http://u", {"a": "b"})
        request.setTransferTimeout.assert_called_once_with(collector.REQUEST_TIMEOUT_MS)

    def test_exception_report_with_http_200_is_still_an_error(self):
        with self.request_mock(*[(0, self.REFUSED.replace(b"permitted output format", b"broken"), "")] * 3):
            with self.assertRaises(WfsError):
                collector.get_features("http://u", {"TYPENAMES": "Z"})


if __name__ == "__main__":
    unittest.main()
