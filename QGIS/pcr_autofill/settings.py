"""Plugin settings, stored inside the QGIS project (so they follow the .qgz file)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Optional

from qgis.core import QgsProject

SCOPE = "pcr_autofill"
KEY = "settings"

MODE_OFF = "off"
MODE_ASK = "ask"
MODE_AUTO = "auto"
MODES = (MODE_OFF, MODE_ASK, MODE_AUTO)

STATUS_FIELD = "pcr_statut"


@dataclass
class Settings:
    mode: str = MODE_ASK                 # what happens when a feature is created
    last_active_mode: str = MODE_ASK     # mode restored by the toolbar toggle
    layer_ids: list = field(default_factory=list)   # layers watched for new features
    covariates: Optional[list] = None    # enabled covariable_cible names ; None = all usable ones
    overwrite: bool = False              # replace values that are already filled in
    status_field: bool = True            # maintain a `pcr_statut` column (ok / partiel / erreur)
    config_path: str = ""                # covariate file ; empty = the one shipped with the plugin
    elevation: bool = True               # altitude (IGN REST API) : fixed exception, see elevation.py

    def copy(self) -> "Settings":
        return Settings(**json.loads(json.dumps(asdict(self))))

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        defaults = cls()
        known = {name: data[name] for name in asdict(defaults) if name in data}
        settings = cls(**known)
        if settings.mode not in MODES:
            settings.mode = defaults.mode
        if settings.last_active_mode not in (MODE_ASK, MODE_AUTO):
            settings.last_active_mode = defaults.last_active_mode
        settings.layer_ids = [str(layer_id) for layer_id in (settings.layer_ids or [])]
        if settings.covariates is not None:
            settings.covariates = [str(name) for name in settings.covariates]
        return settings


def load(project: Optional[QgsProject] = None) -> Settings:
    project = project or QgsProject.instance()
    text, found = project.readEntry(SCOPE, KEY, "")
    if not found or not text:
        return Settings()
    try:
        return Settings.from_dict(json.loads(text))
    except (ValueError, TypeError):
        return Settings()


def save(settings: Settings, project: Optional[QgsProject] = None) -> None:
    project = project or QgsProject.instance()
    project.writeEntry(SCOPE, KEY, json.dumps(asdict(settings)))
