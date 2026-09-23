"""Covariate definitions (couches_selection.json) and the attribute columns they produce.

Column naming follows the convention of env_collector.py :

    intersection_point   1 target field  -> <covariable_cible>
                         n target fields -> <covariable_cible>__<field>  (one per field)
    intersection_bbox    -> <covariable_cible> (distance, km, double)
                            + <covariable_cible>__<field> for every target field
    count_radius         -> <covariable_cible> (integer)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

QUERY_POINT = "intersection_point"
QUERY_NEAREST = "intersection_bbox"
QUERY_COUNT = "count_radius"
QUERY_TYPES = (QUERY_POINT, QUERY_NEAREST, QUERY_COUNT)

DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "couches_selection.json")

REQUIRED_KEYS = ("couche_nom", "url", "covariable_cible")


class ConfigError(ValueError):
    """The covariate configuration file is missing or malformed."""


@dataclass(frozen=True)
class Column:
    name: str
    kind: str  # "string" | "double" | "int"


@dataclass(frozen=True)
class Covariate:
    layer_name: str          # WFS type name (couche_nom)
    title: str
    provider: str
    url: str
    query_type: str
    target: str              # covariable_cible : main column name / prefix
    fields: tuple            # champs_cibles
    radius_m: int
    usage: str
    note: str

    @property
    def columns(self) -> tuple:
        """Attribute columns written for this covariate (empty = not usable)."""
        target = self.target
        if self.query_type == QUERY_COUNT:
            return (Column(target, "int"),)
        if self.query_type == QUERY_NEAREST:
            return (Column(target, "double"),) + tuple(
                Column(f"{target}__{name}", "string") for name in self.fields)
        if len(self.fields) == 1:
            return (Column(target, "string"),)
        return tuple(Column(f"{target}__{name}", "string") for name in self.fields)

    @property
    def usable(self) -> bool:
        return bool(self.columns)

    def field_column(self, field_name: str) -> str:
        """Column receiving the value of the WFS attribute *field_name*."""
        if self.query_type == QUERY_POINT and len(self.fields) == 1:
            return self.target
        return f"{self.target}__{field_name}"


def load_covariates(path: Optional[str] = None) -> list:
    """Read the covariate list from *path* (default : the file shipped with the plugin)."""
    path = path or DEFAULT_CONFIG
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        raise ConfigError(f"Fichier introuvable : {path}") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"JSON invalide ({path}) : {exc}") from None
    if not isinstance(raw, list):
        raise ConfigError(f"{path} doit contenir une liste de couches")

    covariates, targets = [], set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ConfigError(f"Entrée {index} : objet JSON attendu")
        for key in REQUIRED_KEYS:
            if key not in entry:
                raise ConfigError(f"Entrée {index} ({entry.get('couche_nom', '?')}) : clé « {key} » manquante")
        query_type = entry.get("type_requete", QUERY_POINT)
        if query_type not in QUERY_TYPES:
            raise ConfigError(
                f"Entrée {index} : type_requete « {query_type} » inconnu (attendu : {', '.join(QUERY_TYPES)})")
        target = entry["covariable_cible"]
        if target in targets:
            raise ConfigError(f"covariable_cible « {target} » définie deux fois")
        targets.add(target)
        covariates.append(Covariate(
            layer_name=entry["couche_nom"],
            title=entry.get("couche_titre", entry["couche_nom"]),
            provider=entry.get("fournisseur", ""),
            url=entry["url"],
            query_type=query_type,
            target=target,
            fields=tuple(entry.get("champs_cibles") or ()),
            radius_m=int(entry.get("rayon_m", 5000)),
            usage=entry.get("usage", ""),
            note=entry.get("note", ""),
        ))
    return covariates
