# Sentinelle-Or

En une phrase : **Sentinelle-Or regarde régulièrement la Côte d'Ivoire depuis l'espace, repère les
endroits où la végétation disparaît anormalement vite, et prévient l'analyste avec la preuve en
images.**

Détection satellite de l'orpaillage clandestin à partir des images Sentinel-2 et Landsat, par
analyse de la perte de végétation (ΔNDVI jugé au **khi²**), scoring du risque et restitution sur une
carte avec fiche détaillée par zone.

## Ce qui se passe, dans l'ordre

1. **Récupération des images.** Toutes les six heures, le programme vérifie si Sentinel-2 ou Landsat
   ont pris une nouvelle photo d'une zone surveillée (Hiré, Kokumbo, Dabakala…). Si oui, il la
   télécharge et la normalise.
2. **Suppression des nuages.** Un nuage ou son ombre fait croire que la végétation a disparu. Ils
   sont masqués dès l'acquisition, avec une marge de 100 m autour, pour ne pas les confondre avec une mine.
3. **Calcul du NDVI.** Pour chaque pixel de 10 m, un indice entre −1 et 1 mesure la quantité de
   végétation (forêt dense ≈ 0,8, sol nu ≈ 0,1).
4. **Comparaison avec une date antérieure — le khi².** Pour chaque pixel, on calcule
   `d = NDVI_après − NDVI_avant`. Plutôt qu'un seuil fixe, on compare chaque pixel à la **normalité de
   la zone** : baisse médiane, dispersion habituelle. Le khi² mesure l'écart à cette normalité. Un
   pixel n'est retenu que s'il baisse **beaucoup plus que ses voisins** et si sa baisse dépasse aussi
   **0,20** en valeur absolue.
5. **Élimination des faux positifs.** Les pixels retenus sont regroupés en zones. On écarte celles qui
   sont trop petites, trop grandes, trop régulières (champ labouré) ou trop sombres (brûlis).
6. **Note sur 100.** Chaque zone reçoit un score selon l'ampleur de la perte, l'irrégularité de sa
   forme, sa proximité avec une rivière et une éventuelle récidive. Trois niveaux : surveillance,
   modérée, urgente.

L'analyste voit une carte avec les zones colorées selon leur niveau, clique sur l'une d'elles et
obtient sa **fiche** : coordonnées, surface, images avant / après, et le détail du score.

## Pourquoi le khi² change tout

En pleine saison sèche (harmattan), la végétation baisse **partout**. Supposons une baisse médiane de
−0,25 avec une dispersion de 0,06.

| Pixel | Baisse `d` | Seuil fixe (−0,30) | khi² = ((d − médiane)/σ)² | Décision khi² |
|-------|-----------|--------------------|---------------------------|---------------|
| Brousse desséchée | −0,33 | **signalé** (fausse alerte) | (−0,08/0,06)² = **1,8** | ignoré (< 6,63) |
| Mine | −0,65 | signalé | (−0,40/0,06)² = **44** | retenu (≫ 6,63) |

Le seuil fixe ne fait pas la différence entre une saison sèche et une destruction. Le khi² juge chaque
baisse par rapport à ce qui est normal dans la zone, au même moment. Le seuil de **6,63** correspond au
khi² à 1 degré de liberté pour p = 0,01 (99 %).

## Correspondance tâches → code

| Tâche | Fichiers |
|-------|----------|
| Initialiser l'application React avec le fond de carte Leaflet | [`frontend/src/App.tsx`](frontend/src/App.tsx), [`frontend/src/components/MapView.tsx`](frontend/src/components/MapView.tsx) |
| Récupérer les images Sentinel-2 et Landsat de la zone pilote | [`backend/app/processing/acquisition.py`](backend/app/processing/acquisition.py) |
| Ramener les deux sources sur la même grille et masquer les nuages | `acquisition.py` (`EarthSearchProvider.grid`, masque SCL / QA, `write_normalized`) + `ndvi_processing.py` (reprojection WarpedVRT) |
| Calculer la différence de NDVI entre deux dates (khi²) | [`backend/app/processing/ndvi_processing.py`](backend/app/processing/ndvi_processing.py) (`compute_delta_ndvi`, `chi2_change_mask`, `detect_changes`) |
| Fixer le seuil de détection et extraire les zones suspectes en GeoJSON | `ndvi_processing.py` (`chi2_change_mask`, `vectorize_mask`, `extract_zones_geojson`) |
| Créer la base PostgreSQL et le schéma de stockage des zones | [`scripts/init_db.sql`](scripts/init_db.sql), [`backend/app/models.py`](backend/app/models.py), [`backend/app/utils/database.py`](backend/app/utils/database.py) |
| API qui sert les zones et les images avant/après | [`backend/app/api/zones.py`](backend/app/api/zones.py), [`backend/app/main.py`](backend/app/main.py) |
| Afficher les zones de l'API sur la carte | [`frontend/src/components/MapView.tsx`](frontend/src/components/MapView.tsx) |
| Développer la fiche d'une zone | [`frontend/src/components/ZoneDetail.tsx`](frontend/src/components/ZoneDetail.tsx) |

Pipeline complet (orchestration) : [`backend/app/processing/pipeline.py`](backend/app/processing/pipeline.py).

## Démarrage

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1. Récupérer l'historique Sentinel-2 des zones surveillées (sans compte) puis détecter
python -m app.processing.pipeline --backfill

# 2. Lancer l'API
uvicorn app.main:app --reload --port 8000
```

En SQLite par défaut (fichier `sentinelle.sqlite`). Pour PostGIS : `docker compose up` (le schéma
`scripts/init_db.sql` est appliqué au premier démarrage), puis définir `DATABASE_URL`.

### Frontend

```bash
cd frontend
npm install
npm run dev     # http://localhost:5173 (proxy /api → http://localhost:8000)
```

## Principales routes de l'API

| Route | Rôle |
|-------|------|
| `GET /api/zones` | zones suspectes en GeoJSON (carte) |
| `GET /api/zones/{id}` | fiche détaillée d'une zone |
| `GET /api/zones/{id}/comparison` | URLs des vignettes avant / après |
| `GET /api/zones/{id}/thumbnail/{before\|after}.png` | image satellite (vraie couleur ou NDVI) |
| `GET /api/tiles/{ndvi\|truecolor}/{z}/{x}/{y}.png` | fond raster Sentinel-2 daté |
| `GET /api/health` | état du système |

## Pile technique

- **Backend** : Python, FastAPI, SQLAlchemy, rasterio / shapely / pyproj, NumPy, Pillow.
- **Base** : PostgreSQL + PostGIS (SQLite en dev).
- **Frontend** : React 18, TypeScript, Vite, Leaflet / react-leaflet.
- **Données** : Sentinel-2 L2A (Earth Search AWS, sans compte ; ou Copernicus Data Space),
  Landsat 8/9 C2 L2 (USGS M2M).
