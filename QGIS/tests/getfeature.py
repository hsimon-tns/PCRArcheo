import utils
from pprint import pp as pprint

params = {
    "SERVICE":      "WFS",
    "VERSION":      "2.0.0",
    "REQUEST":      "GetFeature",
    "TYPENAMES":    "ELEVATION.CONTOUR.LINE:courbe",
    "SRSNAME":      "EPSG:4326",
    "OUTPUTFORMAT": "application/json",
    "COUNT":        "100",
}

URL = "https://data.geopf.fr/wfs/ows"
COORDS = (-1.64847, 47.17764)
DELTA = 0.001
BBOX = [COORDS[0]-DELTA, COORDS[1]-DELTA, COORDS[0]+DELTA, COORDS[1]+DELTA]
BBOX = [str(b) for b in BBOX]
params["BBOX"] = ",".join(BBOX)+",EPSG:4326"

ret = utils._get(URL, params)
feats = ret["features"]
for f in feats:
    id = f["id"]
    points = f["geometry"]["coordinates"]
    points = [str(p) for p in points]
    data = ',\n'.join(points)
    with open(f"{id}.txt", "w") as file:
        file.write(data)