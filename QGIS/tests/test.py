
import utils
import numpy as np
from shapely.geometry import shape, Point as ShapelyPoint

params = {
    "SERVICE":      "WFS",
    "VERSION":      "2.0.0",
    "REQUEST":      "GetFeature",
    "TYPENAMES":    "",
    "SRSNAME":      "EPSG:4326",
    "OUTPUTFORMAT": "application/json",
    "COUNT":        "10"
}

features = [
    "BDCARTO_V5:occupation_du_sol"
]

point = (-1.55916080407713098, 47.22029314638393771)
DELTA = 0.0001

bbox = [point[0]-DELTA, point[1]-DELTA, point[0]+DELTA, point[1]+DELTA]
bboxStr = [str(b) for b in bbox]
params["BBOX"] = ",".join(bboxStr)+",EPSG:4326"

geojsonbbox = [
    [bbox[0], bbox[1]],
    [bbox[0], bbox[3]],
    [bbox[2], bbox[3]],
    [bbox[2], bbox[1]],
    [bbox[0], bbox[1]]
]

print(geojsonbbox)

point = ShapelyPoint(point)

URL = "https://data.geopf.fr/wfs/ows"
for f in features:
    params["TYPENAME"] = f
    result = None
    while not result:
        result = utils._get(URL, params)
        if not result:
            print("\tCannot get result for this layer...")
            bbox[0] -= DELTA
            bbox[1] -= DELTA
            bbox[2] += DELTA
            bbox[3] += DELTA
            bboxStr = [str(b) for b in bbox]
            params["BBOX"] = ",".join(bboxStr)+",EPSG:4326"
            print(f"Restarting wih bbox : {bbox}")
        else:
            feats = result["features"]
            feats = utils._find_containing_feature(feats, point.x, point.y)
            print(f"Matching : {len(feats)}")
            for f in feats:
                props = f.get("properties", [])
                nature = props.get("nature")
                print(nature)
            '''
                geom = f["geometry"]
                print(geom["type"])
                #poly = geom["coordinates"]
                poly = shape(geom)
                contains = poly.contains(point)
                print(f'Contains = {contains}')
                #poly = np.array(f["geometry"]["coordinates"])
                #print(poly.shape)
            '''