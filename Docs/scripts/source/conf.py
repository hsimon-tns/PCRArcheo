# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import sys
import os

# Project root (D:\DEV\Archeo), resolved from this file's own location so
# the build works regardless of the directory `make`/`sphinx-build` is run
# from (Docs/scripts/source -> Docs/scripts -> Docs -> project root).
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
sys.path.insert(0, PROJECT_ROOT)

# Some UI/*.py modules import each other as bare top-level names
# (e.g. dictview.py does "from node import Node"), relying on UI/ itself
# being on sys.path rather than just the project root.
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'UI'))

# autodoc_mock_imports (below) does not stop owslib from being genuinely
# imported once real PySide6 is loaded in the same process (its shiboken
# compatibility hook then inspects every subsequently imported module,
# and crashes on owslib.wfs : "ValueError: wrapper loop when unwrapping").
# Stub it out directly so its real code never executes during the build.
from unittest.mock import MagicMock
for _mod in ("owslib", "owslib.etree", "owslib.wfs", "owslib.wms"):
    sys.modules.setdefault(_mod, MagicMock())

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'PCR'
copyright = '2026, Hugo Simon'
author = 'Hugo Simon'
release = '0.0.1'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    'myst_parser',
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
    'sphinx.ext.todo',
    'sphinx.ext.githubpages'
]

# Third-party runtime dependencies are not required just to build the docs:
# autodoc mocks them out instead of importing the real packages.
autodoc_mock_imports = [
    'PySide6', 'requests', 'xmltodict', 'shapely', 'owslib',
]
autodoc_member_order = 'bysource'
autodoc_default_options = {
    'members': True,
    'undoc-members': True,
}

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']

source_suffix = {
    '.rst': 'restructuredtext',
    '.md': 'markdown',
}

todo_include_todos = True