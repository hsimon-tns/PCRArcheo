"""Logging to QGIS' message log panel (Log Messages > "PCR Autofill" tab)."""
from qgis.core import Qgis, QgsMessageLog

TAG = "PCR Autofill"

_LEVEL = getattr(Qgis, "MessageLevel", Qgis)


def log(message, level=None):
    """Thread-safe : may be called from a QgsTask worker thread."""
    QgsMessageLog.logMessage(str(message), TAG, _LEVEL.Info if level is None else level)


def warn(message):
    log(message, _LEVEL.Warning)
