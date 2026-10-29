# API Archeo

Outils d'exploitation des API de données Géographiques Françaises pour la collecte automatique de données environnementales.

Le dépôt contient **deux livrables indépendants** :

| Livrable | Description | Où |
|---|---|---|
| **Plugin QGIS « PCR Autofill »** | Remplit automatiquement la table attributaire lors de la création d'un élément dans QGIS. | `QGIS/pcr_autofill/` |
| **Appli bureau « API Géoplateforme »** | Fenêtre Qt pour explorer les services manuellement : choisir un serveur, une couche et des coordonnées. Une carte indique la zone interrogée et la réponse du serveur est affichée sous la forme d'une arbre interactif. Une console Python est aussi intégrée. | `UI/` |

## Prérequis

- **Plugin QGIS** : QGIS 3.28 ou plus (testé avec QGIS 3.44 LTR sous Windows). Rien d'autre à installer.
- **Appli bureau et scripts** : Python 3.12 ou plus (testé avec 3.14), Windows.
- **Accès Internet** pour tout : les serveurs IGN/BRGM et, pour l'appli bureau, le fond de carte (la
  bibliothèque Leaflet est chargée depuis un CDN au démarrage).

## Plugin QGIS

```bat
python tools/build_plugin_zip.py
```

crée `dist/pcr_autofill-<version>.zip`, à installer dans QGIS par *Extensions > Installer/Gérer les
extensions > Installer depuis un ZIP*. Utilisation, réglages, format de la configuration, tests :
voir **[QGIS/pcr_autofill/README.md](QGIS/pcr_autofill/README.md)**.

## Appliction de bureau

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python UI/mainwindow.py
```

L'application standalone peut être lancée depuis n'importe quel répertoire.
Il s'agit d'une interface d'exploration des données disponibles sur les différents serveurs de données Géoréférencées.

À gauche le choix du serveur, de la couche, de la
projection, coordonnées, taille de la zone et carte ; à droite : la réponse en arbre et une console Python
executée dans le contexte de l'appli (taper `help()`). Les serveurs proposés viennent de
`data/capa_selection.json`. L'interface n'a pas de tests automatiques ; le noyau `common/` en a (voir
« Tests » plus bas).

**Exécutable Windows** (sans installer Python) :

```bat
pip install -r requirements-dev.txt
pyinstaller archeo.spec --noconfirm
```

Résultat : `dist/Archeo/Archeo.exe` (dossier à distribuer en entier). Compter environ 6 minutes, QtWebEngine
est lourd.

## Découverte des services

`Scripts/service_discovery.py` lit le catalogue (GetCapabilities) des serveurs listés dans
`data/servers.json`, écarte les couches dont le nom commence par un préfixe d'exclusion de
`data/discovery_filters.json` et marque comme *pertinentes* celles dont le nom, le titre ou le résumé
contient un de ses mots-clés d'inclusion. Il écrit le catalogue complet en JSON :

```bat
python Scripts/service_discovery.py --output catalogue.json --verbose
```

Les fichiers de `data/` sont trouvés quel que soit le dossier d'où on lance le script ; en revanche un
chemin de sortie relatif comme `catalogue.json` est écrit **dans le dossier courant**. Option `--layers fichier.json`
pour n'exporter que la liste des couches pertinentes, `--all` pour tout afficher à l'écran, `--timeout`
(en secondes) pour les serveurs lents.

## Documentation du code

```bat
pip install -r requirements-dev.txt
sphinx-build -b html Docs/scripts/source Docs/scripts/build/html
```

Ouvrir ensuite `Docs/scripts/build/html/index.html`. Elle couvre l'appli bureau, `common/` et
`service_discovery` ; **pas** le plugin QGIS (documenté par son propre README).

## Tests

Noyau partagé (`common/utils.py`), sans accès réseau, depuis la racine du dépôt :

```bat
python -m unittest discover -s tests -v
```

Le plugin QGIS a ses propres tests, à lancer avec le Python de QGIS : voir `QGIS/pcr_autofill/README.md`.

## Organisation du dépôt

| Dossier ou fichier | Contenu |
|---|---|
| `QGIS/pcr_autofill/` | le plugin QGIS (code, tests, README, configuration des covariables) |
| `QGIS/tests/` | scripts et fichiers d'exploration, **non maintenus** (voir ci-dessous) |
| `tests/` | tests automatiques de `common/` |
| `UI/` | l'appli bureau ; point d'entrée `mainwindow.py` (`spinner.py` est du code tiers sous licence MIT) |
| `common/` | requête HTTP avec reprises, lecture JSON, géométrie (point dans polygone), partagés par `UI/` et `Scripts/` |
| `Scripts/` | `service_discovery.py` |
| `data/` | fichiers de configuration et catalogues de l'appli bureau (voir ci-dessous) |
| `Docs/scripts/` | sources de la documentation Sphinx |
| `tools/` | `build_plugin_zip.py` : fabrique le ZIP du plugin |
| `archeo.spec` | recette PyInstaller de l'exécutable |
| `requirements.txt`, `requirements-dev.txt` | dépendances d'exécution / de développement |

### Fichiers de `data/`

| Fichier | Utilisé par | Remarque |
|---|---|---|
| `servers.json` | `service_discovery.py` | liste des serveurs à sonder |
| `discovery_filters.json` | `service_discovery.py` | mots-clés d'inclusion et d'exclusion des couches |
| `capa_selection.json` | `UI/mainwindow.py` | serveurs et couches proposés dans l'appli ; format hérité, voir ci-dessous |
| `capa_format.json` | rien | décrit un ancien schéma ; à supprimer ou à mettre à jour |
| `wfs_params.json` | rien | paramètres WFS de référence, non référencés par le code actuel |

## Limites connues

À savoir avant de modifier quoi que ce soit :

1. **Une seule implémentation des covariables.** La logique « point dans polygone / distance au plus proche /
   comptage » n'existe plus que dans `QGIS/pcr_autofill/collector.py`, corrigée et testée contre les vrais
   serveurs. `common/` ne garde que le noyau générique (requête HTTP avec reprises, lecture JSON, géométrie).
2. **`QGIS/tests/` contient des fichiers d'exploration** (listes de couches et d'attributs, scripts de tri),
   non maintenus et non référencés par le code. Les anciens scripts d'avant le plugin (`env_collector.py`,
   `qgis_demo.py`, `alti.py`) ont été supprimés ; ils restent consultables via le tag `pre-cleanup`.
3. **Format de `data/capa_selection.json` hybride** (clés en anglais et en français mélangées). Il n'est
   **pas** la sortie directe de `service_discovery.py`, qui produit des listes (`servers`, `layers`) alors que
   l'appli attend des dictionnaires indexés par identifiant. La conversion n'est pas automatisée.
4. **Langues mélangées** (identifiants et libellés en français et en anglais selon les fichiers).
5. **Serveurs externes** : le BRGM refuse le format JSON pour plusieurs couches (`common.utils.send_request`
   et le plugin réessaient alors sans ce paramètre et lisent la réponse par défaut) et sa base manque
   parfois de connexions ; l'IGN peut mettre des dizaines de secondes à répondre. Le plugin signale ces
   cas ; l'appli bureau affiche seulement ce que renvoie le serveur.
6. **Shapefile** : le format limite les noms de champs à 10 caractères ; le plugin refuse proprement et
   conseille un GeoPackage.

## Historique

Le tag git `pre-cleanup` marque l'état du dépôt avant le premier rangement : tout fichier supprimé depuis y
est récupérable (`git show pre-cleanup:<chemin>`).
