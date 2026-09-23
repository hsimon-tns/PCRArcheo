"""Background task querying the covariates (and altitude) of one or several locations."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from qgis.core import QgsFeedback, QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from . import elevation
from .collector import collect
from .logger import warn

REQUEST_DELAY = 0.2   # seconds a worker waits after a request : be polite with public APIs
MAX_PARALLEL = 4      # simultaneous requests (each one asks a different layer)

ALTITUDE_TARGET = "altitude"   # label used for progress/error reporting only ; not a real covariate


@dataclass
class FeatureResult:
    fid: int
    values: dict                                 # column name -> value (None = no data)
    errors: list = field(default_factory=list)   # (covariable_cible, message) of failed queries
    queried: int = 0                             # number of covariates (+ altitude) asked for


class EnrichTask(QgsTask):
    """Queries every covariate for every (fid, lon, lat) item, plus altitude unless disabled.

    Altitude (see elevation.py) is queried alongside the covariates, not one of them : it has its
    own on/off setting and its own fetch hook, since it is not driven by couches_selection.json.

    The covariates of one item are queried concurrently : they hit independent layers, and the
    servers are slow enough (seconds, sometimes a stalled request) for the sum to hurt.
    ``featureDone`` is emitted from the worker thread once per item ; Qt delivers it to the main
    thread. The task never touches a layer : the caller applies the results.
    """

    featureDone = pyqtSignal(object)

    def __init__(self, description, items, covariates, fetch=None, delay=None,
                 include_elevation=False, elevation_fetch=None):
        super().__init__(description, QgsTask.CanCancel)
        self._items = list(items)
        self._covariates = list(covariates)
        self._fetch = fetch
        self._include_elevation = include_elevation
        self._fetch_elevation = elevation_fetch
        self._delay = REQUEST_DELAY if delay is None else delay
        self._feedback = QgsFeedback()

    def _query(self, covariate, lon, lat) -> dict:
        try:
            return collect(covariate, lon, lat, self._fetch, self._feedback)
        finally:
            if self._delay:
                time.sleep(self._delay)

    def _query_elevation(self, lon, lat) -> dict:
        try:
            return elevation.collect(lon, lat, self._fetch_elevation, self._feedback)
        finally:
            if self._delay:
                time.sleep(self._delay)

    def run(self) -> bool:
        per_item = len(self._covariates) + int(self._include_elevation)
        steps = max(len(self._items) * per_item, 1)
        done = 0
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
            for fid, lon, lat in self._items:
                if self.isCanceled():
                    return False
                futures = [(covariate.target, pool.submit(self._query, covariate, lon, lat))
                           for covariate in self._covariates]
                if self._include_elevation:
                    futures.append((ALTITUDE_TARGET, pool.submit(self._query_elevation, lon, lat)))
                values, errors = {}, []
                for target, future in futures:   # in configuration order : deterministic result
                    try:
                        values.update(future.result())
                    except Exception as exc:   # one failing server must not stop the others
                        errors.append((target, str(exc)))
                        warn(f"{target} ({lon:.5f}, {lat:.5f}) : {exc}")
                    done += 1
                    self.setProgress(100.0 * done / steps)
                if self.isCanceled():
                    return False
                self.featureDone.emit(FeatureResult(fid, values, errors, per_item))
        return True

    def cancel(self):
        self._feedback.cancel()   # aborts the requests in flight
        super().cancel()
