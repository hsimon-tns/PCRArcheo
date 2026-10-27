"""Fabrique le ZIP installable du plugin QGIS « PCR Autofill ».

    python tools/build_plugin_zip.py

Produit dist/pcr_autofill-<version>.zip (la version est lue dans metadata.txt), à installer dans QGIS via
Extensions > Installer/Gérer les extensions > Installer depuis un ZIP.

QGIS exige un dossier racine unique dont le nom est celui du module (ici « pcr_autofill »). Les tests et les
caches Python sont exclus : ils ne servent pas à l'utilisateur final. N'utilise que la bibliothèque standard.
"""
import configparser
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "QGIS" / "pcr_autofill"
OUTPUT_DIR = ROOT / "dist"
EXCLUDED_DIRS = {"__pycache__", "tests"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
REQUIRED_METADATA = ("name", "qgisMinimumVersion", "description", "version", "author", "email")


def read_metadata() -> dict:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(PLUGIN_DIR / "metadata.txt", encoding="utf-8")
    metadata = dict(parser["general"])
    missing = [key.lower() for key in REQUIRED_METADATA if key.lower() not in metadata]
    if missing:
        sys.exit(f"metadata.txt : clés obligatoires manquantes : {', '.join(missing)}")
    return metadata


def plugin_files():
    for path in sorted(PLUGIN_DIR.rglob("*")):
        relative = path.relative_to(PLUGIN_DIR)
        if path.is_file() and not EXCLUDED_DIRS.intersection(relative.parts) \
                and path.suffix not in EXCLUDED_SUFFIXES:
            yield path, relative


def main() -> None:
    metadata = read_metadata()
    OUTPUT_DIR.mkdir(exist_ok=True)
    target = OUTPUT_DIR / f"{PLUGIN_DIR.name}-{metadata['version']}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, relative in plugin_files():
            archive.write(path, Path(PLUGIN_DIR.name, relative).as_posix())
    print(f"{target.relative_to(ROOT)}  ({target.stat().st_size / 1024:.0f} Ko)")
    print(f"Plugin « {metadata['name']} » {metadata['version']} (QGIS >= {metadata['qgisminimumversion']})")


if __name__ == "__main__":
    main()
