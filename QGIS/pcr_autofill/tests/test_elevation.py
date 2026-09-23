import unittest
from unittest import mock

from pcr_autofill import elevation
from pcr_autofill.elevation import ElevationError


class CollectTest(unittest.TestCase):
    def test_uses_the_given_fetcher_with_lon_lat_only(self):
        calls = []
        fetch = lambda lon, lat: (calls.append((lon, lat)), 12.94)[-1]
        self.assertEqual(elevation.collect(-1.5536, 47.2184, fetch), {"altitude_m": 12.94})
        self.assertEqual(calls, [(-1.5536, 47.2184)])

    def test_a_failing_fetcher_propagates(self):
        def fetch(lon, lat):
            raise ElevationError("boom")
        with self.assertRaises(ElevationError):
            elevation.collect(0, 0, fetch)


class DefaultFetcherTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(elevation, "time")
        patcher.start()
        self.addCleanup(patcher.stop)

    def request_mock(self, *responses):
        return mock.patch.object(elevation, "_request", side_effect=list(responses))

    def test_sends_lon_lat_resource_and_zonly(self):
        with self.request_mock((0, b'{"elevations": [12.94]}', "")) as request:
            value = elevation.default_fetcher()(-1.5536, 47.2184)
        self.assertEqual(value, 12.94)
        url, params = request.call_args[0][:2]
        self.assertEqual(url, elevation.ELEVATION_URL)
        self.assertEqual(params, {"lon": -1.5536, "lat": 47.2184,
                                  "resource": elevation.ELEVATION_RESOURCE, "zonly": "true"})

    def test_retries_a_transient_failure(self):
        with self.request_mock((1, b"", "timeout"), (0, b'{"elevations": [5.0]}', "")) as request:
            value = elevation.default_fetcher()(0, 0)
        self.assertEqual(value, 5.0)
        self.assertEqual(request.call_count, 2)

    def test_gives_up_after_the_retries_with_the_server_message(self):
        with self.request_mock(*[(3, b"", "server down")] * elevation.MAX_RETRIES):
            with self.assertRaisesRegex(ElevationError, "server down"):
                elevation.default_fetcher()(0, 0)

    def test_unreadable_response_is_a_retriable_failure(self):
        with self.request_mock((0, b"not json", ""), (0, b'{"elevations": [1.0]}', "")) as request:
            value = elevation.default_fetcher()(0, 0)
        self.assertEqual(value, 1.0)
        self.assertEqual(request.call_count, 2)

    def test_missing_elevations_key_gives_none_not_a_crash(self):
        with self.request_mock((0, b"{}", "")):
            self.assertIsNone(elevation.default_fetcher()(0, 0))


if __name__ == "__main__":
    unittest.main()
