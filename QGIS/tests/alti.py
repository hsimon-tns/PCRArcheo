import requests

r = requests.get("https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json",
    params={"lon": -1.64847, "lat": 47.17764, "resource": "ign_rge_alti_wld", "zonly": "true"})
print(r.json())