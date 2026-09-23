import json
from dataclasses import dataclass, field, asdict
from owslib.wfs import WebFeatureService
from owslib.wms import WebMapService

import utils

with open('capa.json', 'r') as f:
    d = json.load(f)

fOk, reason = utils.checkCapaFormat(d)

if fOk:
    print("Capa format is valid !")
else:
    print(f"[ERROR] Invalid capa format : \n\t{reason}")

params = {
    "SERVICE":      "WFS",
    "VERSION":      "2.0.0",
    "REQUEST":      "GetFeature",
    "TYPENAMES":    "",
    "SRSNAME":      "EPSG:4326",
    "OUTPUTFORMAT": "application/json",
    "COUNT":        "100",
    "BBOX":         "-1.8852725718,47.0387146699,-1.2187692802,47.4018490629,EPSG:4326"
}

DEFAULT = "<unknown>"

server_list = d["serveurs"]

# Just one feature now
server_list = [
    {
        "nom": "ign_wfs",
        "url": "https://data.geopf.fr/wfs/ows",
        "nb_couches": 1,
        "couches": [
            {
                "nom": "BDCARTO_V5:occupation_du_sol"
            }
        ]
    }
]

properties = {}
total_layers = 0
nature_list = []

for server in server_list:
    sname = server.get("nom", DEFAULT)
    properties[sname] = {}
    couches = server.get("couches", [])
    url = server.get("url", DEFAULT)
    ncouches = server["nb_couches"]
    total_layers += ncouches
    print(sname)
    print(url)
    for c in couches:
        cname = c.get("nom", DEFAULT)
        print(f"\t{cname}")
    for c in couches:
        cname = c.get("nom", DEFAULT)
        print(f"\t{cname}")
        params["TYPENAME"] = cname
        result = utils._get(url, params)
        if not result:
            print("\tCannot get result for this layer...")
        else:
            feats = result["features"]
            print(f"Received {len(feats)} results")
            for f in feats:
                props = f.get("properties", [])
                print(props)
                properties[sname][cname] = props
                nature_list.append(props["nature"])
                print(type(props["nature"]))

natures = set(nature_list)
print(natures)
print(f"There are {len(natures)} types of ground")
#print(f"Total layers : {total_layers}")