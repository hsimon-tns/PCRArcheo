"""
pcr_tools/service_discovery.py
-------------------------------
Automatic discovery of OGC services available on French
georeferenced data servers (IGN, BRGM).
"""

import json
import time
import os
import sys
from dataclasses import dataclass, field, asdict
from typing import Optional
from datetime import datetime

import requests
from owslib.wfs import WebFeatureService
from owslib.wms import WebMapService

PROJECT_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_PATH not in sys.path:
    sys.path.insert(0, PROJECT_PATH)   # pour « from common import utils » ; pas de os.chdir : chemins absolus

from common import utils

filters = utils.loadJson(os.path.join(PROJECT_PATH, "data", "discovery_filters.json"))
includeKeywords = filters.get("include", {}).get("keywords", [])
excludeKeywords = filters.get("exclude", {}).get("keywords", [])

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _normalise_epsg(crs_list: list) -> list[str]:
    """
    Convert URN OGC in short codes
    urn:ogc:def:crs:EPSG::4326  →  EPSG:4326
    EPSG:4326                   →  EPSG:4326  (unchanged)
    """
    result = []
    seen = set()
    for c in crs_list:
        s = str(c)
        if "::" in s:
            code = s.split("::")[-1]
            normalized = f"EPSG:{code}"
        elif s.upper().startswith("EPSG:"):
            normalized = s.upper()
        else:
            normalized = s
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _is_excluded(name: str) -> bool:
    """Return True if the layer should be ignored (duplicate, out of scope)."""
    name_upper = name.upper()
    return any(name_upper.startswith(p.upper()) for p in excludeKeywords)

def _is_obsolete(title: str) -> bool:
    return any(kw in title.lower() for kw in ["obsolete", "obsolète", "(old)"])


def _detected_keywords(name: str, title: str, abstract: str) -> list[str]:
    """Return the relevant keywords found in the name, title, or abstract."""
    text = f"{name} {title} {abstract}".lower()
    return [kw for kw in includeKeywords if kw.lower() in text]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Layer:
    name:        str
    title:       str
    abstract:    str
    projections: list[str]
    relevant:    bool
    keywords:    list[str]

@dataclass
class ServerResult:
    id:           str
    name:         str
    provider:     str
    service_type: str
    url:          str
    relevance:    str
    status:       str
    layer_count:  int
    layers:       list[Layer]
    error:        Optional[str]
    duration_ms:  int


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _discover_wfs(server: dict, timeout: int, verbose: bool) -> ServerResult:
    t0 = time.time()
    layers = []
    error = None
    status = "ok"

    try:
        wfs = WebFeatureService(url=server["url"], version=server["version"], timeout=timeout)
        for name, meta in wfs.contents.items():
            if _is_excluded(name):
                continue
            title = getattr(meta, "title", "") or name
            if _is_obsolete(title):
                continue
            abstract = getattr(meta, "abstract", "") or ""
            projs    = _normalise_epsg(getattr(meta, "crsOptions", []))
            keywords = _detected_keywords(name, title, abstract)
            layers.append(Layer(
                name=name, title=title, abstract=abstract[:200],
                projections=projs, relevant=bool(keywords), keywords=keywords,
            ))
            if verbose:
                print(f"    [{'✓' if keywords else ' '}] {name}")

    except requests.exceptions.Timeout:
        status, error = "timeout", f"Timeout after {timeout}s"
    except Exception as e:
        status, error = "error", str(e)[:200]

    duration = int((time.time() - t0) * 1000)
    return ServerResult(
        id=server["id"], name=server["name"], provider=server["provider"],
        service_type=server["type"], url=server["url"], relevance=server["relevants"],
        status=status, layer_count=len(layers), layers=layers,
        error=error, duration_ms=duration,
    )


def _discover_wms(server: dict, timeout: int, verbose: bool) -> ServerResult:
    t0 = time.time()
    layers = []
    error = None
    status = "ok"

    try:
        wms = WebMapService(url=server["url"], version=server["version"], timeout=timeout)
        for name, meta in wms.contents.items():
            if _is_excluded(name):
                continue
            title = getattr(meta, "title", "") or name
            if _is_obsolete(title):
                continue
            abstract = getattr(meta, "abstract", "") or ""
            projs    = _normalise_epsg(getattr(meta, "crsOptions", []))
            keywords = _detected_keywords(name, title, abstract)
            layers.append(Layer(
                name=name, title=title, abstract=abstract[:200],
                projections=projs, relevant=bool(keywords), keywords=keywords,
            ))
            if verbose:
                print(f"    [{'✓' if keywords else ' '}] {name}")

    except requests.exceptions.Timeout:
        status, error = "timeout", f"Timeout after {timeout}s"
    except Exception as e:
        status, error = "error", str(e)[:200]

    duration = int((time.time() - t0) * 1000)
    return ServerResult(
        id=server["id"], name=server["name"], provider=server["provider"],
        service_type=server["type"], url=server["url"], relevance=server["relevants"],
        status=status, layer_count=len(layers), layers=layers,
        error=error, duration_ms=duration,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def discover_all(timeout: int = 30, only_relevant: bool = False, verbose: bool = False) -> dict:
    results = []
    servers = utils.loadJson(os.path.join(PROJECT_PATH, "data", "servers.json"))
    for server in servers:
        if verbose:
            print(f"\n{'─'*60}\n  {server['provider']} — {server['name']}\n{'─'*60}")
        r = _discover_wfs(server, timeout, verbose) if server["type"] == "WFS" \
            else _discover_wms(server, timeout, verbose)
        if verbose:
            relevant_count = sum(1 for c in r.layers if c.relevant)
            if r.status == "ok":
                print(f"  → {r.layer_count} layers ({relevant_count} relevant) in {r.duration_ms}ms")
            else:
                print(f"  ✗ {r.status}: {r.error}")
        if only_relevant:
            r.layers = [c for c in r.layers if c.relevant]
        results.append(r)

    summary = {
        "server_count":         len(results),
        "server_ok_count":      sum(1 for r in results if r.status == "ok"),
        "total_layer_count":    sum(r.layer_count for r in results),
        "relevant_layer_count": sum(sum(1 for c in r.layers if c.relevant) for r in results),
    }
    return {"date": datetime.now().isoformat(timespec="seconds"), "servers": [asdict(r) for r in results], "summary": summary}


def export_relevant_layers(catalog: dict, output_path: str) -> None:
    layers = []
    for srv in catalog["servers"]:
        if srv["status"] != "ok":
            continue
        for c in srv["layers"]:
            if not c["relevant"]:
                continue
            layers.append({
                "server":       srv["id"],
                "provider":     srv["provider"],
                "service_type": srv["service_type"],
                "url":          srv["url"],
                "name":         c["name"],
                "title":        c["title"],
                "keywords":     c["keywords"],
            })
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(layers, f, ensure_ascii=False, indent=2)
    print(f"{len(layers)} relevant layers exported → {output_path}")


def print_catalog(catalog: dict, only_relevant: bool = True) -> None:
    r = catalog["summary"]
    print(f"\n{'═'*65}")
    print(f"  OGC CATALOG  —  {catalog['date']}")
    print(f"  {r['server_ok_count']}/{r['server_count']} servers  ·  {r['total_layer_count']} layers  ·  {r['relevant_layer_count']} relevant")
    print(f"{'═'*65}")
    for srv in catalog["servers"]:
        print(f"\n  [{srv['service_type']}] {srv['provider']} — {srv['url']}")
        if srv["status"] != "ok":
            print(f"  ✗ {srv['error']}")
            continue
        layers = [c for c in srv["layers"] if c["relevant"]] if only_relevant else srv["layers"]
        print(f"  {srv['layer_count']} layers  ·  {sum(1 for c in srv['layers'] if c['relevant'])} relevant")
        for c in layers:
            print(f"    ✓ {c['name']}")
            print(f"      {c['title']}")
            print(f"      Tags: {', '.join(c['keywords'])}")
    print(f"\n{'═'*65}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Discovery of French OGC services (v2)")
    parser.add_argument("--output", "-o")
    parser.add_argument("--layers", "-l")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--timeout", "-t", type=int, default=30)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    print("Discovering OGC services…")
    catalog = discover_all(timeout=args.timeout, verbose=args.verbose)
    print_catalog(catalog, only_relevant=not args.all)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(catalog, f, ensure_ascii=False, indent=2)
        print(f"Catalog → {args.output}")
    if args.layers:
        export_relevant_layers(catalog, args.layers)
