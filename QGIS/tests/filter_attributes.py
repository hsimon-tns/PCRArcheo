import re

filter_out = [
    "LIMITES_ADMINISTRATIVES", "ADMINEXPRESS", "limite_administrative", "19e siecle", "Cimetieres",
    "19..-19..", "en 19[0-9]+", "grande echelle", "amiante", "aerodrome", "sport", "par an", "impot"
    "les vosges", "le jura", "corse", "ile de france", "lyon", "pas-de-calais", "normandie", "bordeau", "marseille",
    "martinique", "guadeloupe", "guyane", "mayotte", "gironde", "loiret", "saone", "cantal", "savoie", "pyrenees", "alpes",
    "aisne", "ardeche", "aveyron", "ariege", "calvados", "charente", "correze", "herault", "indre", "isere", "garonne",
    "nievre", "meurthe", "moselle", "mayenne", "marne", "belfort", "outre-mer", "outremer", "part de", "amenage", 
    "touristique", "tourisme", "museum", "etablissements", "potentiel", "installations", "Patrinat", "points d'acces",
    "ecole", "hoptiaux", "hoptial", "police", "gendarmerie", "servitude", "taux", "energie", "festival", "Chantier",
    "pollution", "cimetiere", 
]

with open("attributes.txt", "r") as f:
    data = f.readlines()

f = open("attributes_filtered.txt", "w")
removed = 0
for line in data:
    m = False
    for fo in filter_out:
        if re.search(fo, line, flags=re.IGNORECASE):
            m = True
    if not m:
        f.write(line)
    else:
        removed += 1
f.close()

print(f'Removed {removed} out of {len(data)}')