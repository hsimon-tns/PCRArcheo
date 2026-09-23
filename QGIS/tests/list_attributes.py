import json

PATH = "capa.json"

with open(PATH, 'r') as f:
    data = json.load(f)

servers = data["serveurs"]

for s in servers:
    print(s["nom"])
    for c in s["couches"]:
        name = c["nom"]
        summary = c["resume"]
        print(f"\t{name}  : {summary}")