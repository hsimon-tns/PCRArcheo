# PCR Autofill — extension QGIS

Remplit la table attributaire d'une couche avec des **covariables environnementales** (distance au
cours d'eau, occupation du sol, aléa argile, mouvements de terrain…) interrogées sur les services WFS de
l'IGN et du BRGM, **au moment où l'on crée un élément** — ou à la demande sur les éléments sélectionnés.

Testé avec QGIS 3.44 (Windows). Ne dépend d'aucun paquet Python à installer : réseau, géométrie et
lecture GeoJSON/GML utilisent QGIS et GDAL.

## Installation

**Pour l'utiliser (depuis un ZIP).** Depuis la racine du dépôt, créez le ZIP :

```bat
python tools/build_plugin_zip.py
```

Il est créé dans `dist/pcr_autofill-<version>.zip`. Dans QGIS : *Extensions > Installer/Gérer les
extensions > Installer depuis un ZIP*, choisissez ce fichier, puis vérifiez que *PCR Autofill* est coché
dans l'onglet *Installées*. Pour mettre à jour : refaites la même opération avec le nouveau ZIP.

**Pour le modifier (lien vers le dépôt).** Un lien de répertoire évite de recopier le code à chaque
modification (pas besoin d'être administrateur). Remplacez `<dépôt>` par le chemin du dépôt :

```bat
mklink /J "%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\pcr_autofill" "<dépôt>\QGIS\pcr_autofill"
```

Si un dossier `pcr_autofill` existe déjà à cet endroit (installation par ZIP), supprimez-le d'abord, sans
quoi il y aurait deux exemplaires du plugin et QGIS chargerait celui qui n'est plus à jour. Avec
*Plugin Reloader*, une modification du code se recharge sans redémarrer QGIS.

## Barre d'outils

L'extension ajoute une barre d'outils **PCR Autofill** (et un menu *Extensions > PCR Autofill*) :

| Bouton | Rôle |
|---|---|
| Remplissage à la création | interrupteur : coupe / rétablit le remplissage à la création d'un élément |
| Remplir les éléments sélectionnés | remplit les éléments sélectionnés de la couche active |
| Configuration… | ouvre le dialogue de réglages |

## Utilisation

1. **Configuration…** : cochez la ou les couches à surveiller, choisissez les covariables et le mode.
2. Créez un élément (outil *Ajouter un point*, collage, import…). Selon le mode :
   - **Automatique** : le remplissage démarre seul ;
   - **Demander** : un bandeau propose « Remplir » ;
   - **Désactivé** : rien ne se passe.
3. Le remplissage tourne en arrière-plan (suivi dans le panneau des tâches, annulable). Les valeurs
   arrivent dans la couche au fur et à mesure.

Les valeurs sont écrites dans le **tampon d'édition** : la couche reste en édition, vous relisez, vous
pouvez annuler (`Ctrl+Z` annule le remplissage d'un élément d'un coup) et vous enregistrez avec le reste.
Si la couche est enregistrée avant la fin des requêtes, l'élément est retrouvé par sa géométrie et
rempli quand même.

**Champs manquants.** Les colonnes n'existent pas d'office : l'extension propose de les créer (bandeau ou
bouton *Créer les champs manquants*) — jamais sans accord, c'est une modification du schéma. Préférez un
**GeoPackage** : un shapefile tronque les noms à 10 caractères (`dist_cours_eau_km`…) et l'extension
le signale.

**Réglages** (enregistrés dans le projet `.qgz`) : mode, couches surveillées, covariables actives,
« remplacer les valeurs déjà renseignées » (non par défaut : une valeur saisie à la main est conservée),
champ d'état, fichier de configuration.

**Champ `pcr_statut`** (optionnel) : `ok`, `partiel` (une covariable en erreur, les autres remplies) ou
`erreur`. Il distingue une panne réseau d'une absence de donnée : sans lui, les deux donnent NULL.

## Configuration des covariables

`data/couches_selection.json` est fourni avec l'extension ; on peut en désigner un autre dans le
dialogue. Une entrée :

```json
{
  "couche_nom": "BDTOPO_V3:cours_d_eau",
  "couche_titre": "BDTOPO : Cours d'eau",
  "fournisseur": "IGN",
  "url": "https://data.geopf.fr/wfs/ows",
  "type_requete": "intersection_bbox",
  "covariable_cible": "dist_cours_eau_km",
  "champs_cibles": ["toponyme", "importance"],
  "rayon_m": 5000
}
```

| `type_requete` | Résultat | Colonnes créées |
|---|---|---|
| `intersection_point` | attributs du polygone qui contient le point | `covariable_cible` (1 champ) ou `covariable_cible__champ` |
| `intersection_bbox` | distance (km) à l'objet le plus proche + ses attributs | `covariable_cible` (réel) + `covariable_cible__champ` |
| `count_radius` | nombre d'objets à moins de `rayon_m` mètres | `covariable_cible` (entier) |

Une entrée `intersection_point` **sans `champs_cibles`** est ignorée (grisée dans le dialogue) : on ne
sait pas quelles colonnes créer. Un nom de `champs_cibles` que le serveur ne connaît pas donne NULL pour
cette colonne, sans faire échouer le reste.

## Altitude — l'exception

**L'altitude n'est PAS une covariable** : elle n'apparaît pas dans `couches_selection.json` ni dans la
liste « Covariables à interroger » du dialogue, et n'a pas de `type_requete`. Toutes les covariables
ci-dessus interrogent une couche WFS ; l'altitude vient d'une **API REST** différente de l'IGN (calcul
d'altimétrie sur le RGE ALTI), qui prend juste un `lon`/`lat` et répond une valeur — pas de notion de
couche, de géométrie en retour ou de `champs_cibles`. Plutôt que de forcer cette forme dans le système
piloté par JSON, elle est câblée en dur dans **`elevation.py`**, un module séparé et documenté comme tel
en tête de fichier.

Concrètement, pour l'utilisateur : une case à cocher **à part**, dans son propre encadré « Altitude —
exception (pas une covariable) » du dialogue de configuration, activée par défaut. Colonne créée :
`altitude_m` (réel, en mètres). Elle est toujours interrogée en même temps que les covariables (même
tâche, même parallélisme), et compte dans `pcr_statut` comme les autres (une altitude en échec pendant
que le reste réussit donne `partiel`).

## Ce que fait le collecteur (et pourquoi)

Vérifié sur les serveurs réels de l'IGN et du BRGM :

- **`BBOX` en forme URN** (`minLat,minLon,maxLat,maxLon,urn:ogc:def:crs:EPSG::4326`). La forme courte
  `…,EPSG:4326` est lue `lon,lat` par l'IGN (GeoServer) mais `lat,lon` par le BRGM (MapServer) : avec
  l'ordre `lat,lon` l'IGN renvoie **zéro entité**. L'URN est sans ambiguïté et acceptée des deux.
- **`PROPERTYNAME` supprime la géométrie** côté serveur. Il n'est donc utilisé que pour la stratégie
  « valeur au point », où seuls les attributs comptent ; les autres stratégies récupèrent l'objet entier.
- **BRGM refuse le JSON** (`'application/json' is not a permitted output format`) : l'extension retombe
  sur son format par défaut (GML), lu par GDAL comme le GeoJSON.
- **« Valeur au point »** : le serveur fait lui-même le point-dans-polygone (fenêtre de ±1 m, attributs
  seuls) au lieu de télécharger les polygones — leurs géométries pèsent des mégaoctets.
- **Distances** mesurées en mètres vraies dans une projection équidistante centrée sur le point, jusqu'au
  point le plus proche de l'objet (et non jusqu'à son sommet le plus proche).
- Requêtes : délai de 30 s, 3 essais (réponse vide ou erreur serveur incluses), jusqu'à 4 en parallèle.

## Limites connues

- Nécessite un accès Internet aux serveurs (proxy et authentification de QGIS respectés).
- Le serveur BRGM (`geoservices.brgm.fr/risques`) manque parfois de connexions à sa base : la covariable
  est alors en erreur (`pcr_statut = partiel`) ; relancez plus tard avec *Remplir les éléments sélectionnés*.
- `count_radius` est plafonné à 1000 objets par requête (un avertissement est journalisé s'il est atteint).
- Éléments non ponctuels : le point interrogé est `pointOnSurface()` de la géométrie.
- Journal détaillé : *Vue > Panneaux > Messages du journal*, onglet **PCR Autofill**.

## Tests

Les tests s'exécutent avec le Python **fourni par QGIS** (c'est lui qui contient `qgis.core` et GDAL), depuis
le dossier `QGIS` du dépôt. `<QGIS>` désigne le dossier d'installation de QGIS (par exemple
`C:\Program Files\QGIS 3.44.11`) ; le lanceur s'appelle `python-qgis-ltr.bat` pour une version LTR et
`python-qgis.bat` sinon.

Sans réseau (QGIS en mode headless, faux serveur WFS) :

```bat
cd <dépôt>\QGIS
"<QGIS>\bin\python-qgis-ltr.bat" -m unittest discover -s pcr_autofill/tests -t .
```

Contre les vrais serveurs IGN/BRGM (à utiliser avec parcimonie) :

```bat
set PCR_LIVE=1
"<QGIS>\bin\python-qgis-ltr.bat" -m unittest pcr_autofill.tests.test_live -v
```

## Organisation

| Fichier | Rôle |
|---|---|
| `plugin.py` | barre d'outils, menu, interrupteur |
| `autofill.py` | surveille les couches, file de travaux, écriture dans la couche |
| `tasks.py` | `QgsTask` : interroge les covariables en arrière-plan |
| `collector.py` | requêtes WFS et les trois stratégies (aucun accès à la couche ni à l'interface) |
| `covariates.py` | lecture de `couches_selection.json`, noms des colonnes |
| `elevation.py` | l'exception altitude (API REST IGN, hors `couches_selection.json`) |
| `settings.py`, `settings_dialog.py` | réglages (dans le projet) et dialogue |
