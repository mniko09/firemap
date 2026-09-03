# FIREMAP v2 — Architecture, outils & dépannage

Document de référence pour comprendre le code et savoir **où intervenir selon le
problème rencontré**.

---

## 1. Vue d'ensemble

```
[Navigateur]  web/index.html (Leaflet)
     │  recherche commune, clic, sliders
     ▼
[API FastAPI]  src/firemap/api/
     │  lit le REGISTRE (SQLite) : commune prête ? à jour ? en cours ?
     ├── prête   → sert tuiles COG / GeoJSON / valeur-pixel (cache disque)
     ├── absente → met un job en file, répond "en cours"
     ▼
[Worker in-process]  src/firemap/jobs.py  (ThreadPool, 2 max)
     │  exécute  src/firemap/pipeline.py : run(insee)
     ├── contour (geo.api.gouv.fr) → grille
     ├── NDVI/NDMI (Sentinel-2 / Copernicus)
     ├── pente/expo (MNT IGN) · végétation (IGN) · enjeux (Géorisques + éduc)
     ├── FWI (Météo-France DPClim)
     ├── fusion pondérée + quantiles + priorisation
     └── COG web-optimisés
     ▼
[Disque]  data/communes/<INSEE>/{boundaries,raw,processed}/  +  data/firemap.sqlite
     ▲
[Planificateur]  src/firemap/scheduler.py  (APScheduler, toutes les 12 h)
     └── détecte donnée Sentinel-2 / météo plus récente → relance le pipeline
```

Convention CRS : **calculs en Lambert-93 (EPSG:2154)**, **affichage en WGS84 (EPSG:4326)**.

---

## 2. Arborescence du code (`src/firemap/`)

| Fichier | Rôle |
|---|---|
| `config.py` | constantes : chemins (`COMMUNES_DIR`…), CRS, résolution 10 m. Crée `data/` à l'import. |
| `context.py` | **`CommuneContext(insee)`** : identité + chemins isolés `data/communes/<INSEE>/…`. Remplace la « commune globale » du v1. |
| `http.py` | **`SESSION`** : `requests.Session` partagée, retry ×3 + backoff, timeouts. Tous les appels HTTP du pipeline passent par là. |
| `departements.py` | régions métropole + Corse + limitrophes. `search_batches()` élargit la recherche de station météo. |
| `registry.py` | **cache d'état SQLite** (`data/firemap.sqlite`). Cycle : `queued→running→ready` / `error` / `stale`. Dates Sentinel-2 & FWI, horodatage. |
| `pipeline.py` | **orchestrateur** : `run(insee, force=False)`, 8 étapes idempotentes (gardes par date de modif `_outdated`). Écrit sous `ctx.processed_dir`, met le registre à jour. |
| `jobs.py` | exécution en tâche de fond : `ThreadPoolExecutor(2)`, `submit(insee)`, `reset_orphans()` au démarrage. |
| `scheduler.py` | `BackgroundScheduler` APScheduler, `refresh_scan()` toutes les 12 h, `ensure_priority_communes()`. |
| `freshness.py` | « donnée plus récente dispo ? » — Sentinel-2 via catalogue Copernicus (STAC), FWI via arithmétique de dates. |
| `grid.py` | grille gabarit (emprise commune arrondie à 10 m), rasterisation du masque, `align_to_grid`. |
| `storage.py` | **`LAYERS`** : liste des 9 couches + symbologie (colormap, plage). Source unique de vérité pour l'affichage. |
| `ingestion/commune.py` | contour officiel via `geo.api.gouv.fr`, **recherche par code INSEE** (le nom est ambigu). |
| `ingestion/sentinel2.py` | NDVI/NDMI calculés côté serveur Copernicus (evalscript), masque nuage SCL. Config OAuth CDSE. |
| `ingestion/mnt.py` | MNT IGN RGE ALTI (WMS) → pente & exposition par différences finies. |
| `ingestion/landcover.py` | végétation IGN BD TOPO (WFS) → poids combustible par type (à dire d'expert). |
| `ingestion/enjeux.py` | ICPE/SEVESO (Géorisques) + écoles (education.gouv.fr) → colonne `nom` + `categorie`. |
| `ingestion/fwi.py` | stations Météo-France DPClim ; `compute_fwi_series` = système canadien Forêt-Météo (Van Wagner). |
| `ingestion/bdiff.py` | lecture de l'export BDIFF (CSV `;`, UTF-8 avec repli cp1252, 24 colonnes). |
| `risk/fusion.py` | normalisation 0-1, **pondération** (sécheresse 30 / FWI 20 / combustible 20 / pente 15 / expo 15), 4 classes par **quantiles**. |
| `risk/priorisation.py` | `risque × proximité_enjeux` → 15 zones prioritaires + action recommandée. |
| `api/main.py` | app FastAPI, lifespan (registre + orphelins + planificateur), `/api/health`, `/api/communes`, `/api/refresh/scan`, static `web/`. |
| `api/routes_communes.py` | `/api/communes/search`, `/{insee}/status`, `/{insee}/generate`. |
| `api/routes_layers.py` | `/{insee}/layers`, `…/layers/{id}/{z}/{x}/{y}.png` (tuiles rio-tiler), `bounds`, `metadata`, `priorites`, `commune`, `value`. |

`scripts/` : `phase*.py` (v1, mono-commune, historiques) · `loadtest.py` (test de charge).
`tests/` : 17 tests unitaires **sans réseau** (`pytest`).

---

## 3. Bibliothèques & pourquoi

| Outil | Usage | Pourquoi |
|---|---|---|
| **FastAPI + Uvicorn** | API + service statique | continuité v1, async, `/docs` auto |
| **rasterio / numpy / scipy** | lecture/écriture GeoTIFF, calculs raster | standard géospatial, wheels avec GDAL embarqué |
| **geopandas / shapely / pyproj** | vecteurs, reprojection L93↔WGS84 | idem |
| **sentinelhub** | Sentinel-2 via Copernicus (Processing + Catalog API) | calcul NDVI/NDMI côté serveur, pas de téléchargement d'images brutes |
| **rio-cogeo** | conversion en Cloud-Optimized GeoTIFF web-optimisé | tuilage interne aligné XYZ |
| **rio-tiler** | rendu d'une tuile 256×256 à la volée depuis un COG | pas de pré-génération de millions de PNG |
| **APScheduler** | planificateur in-process | simple, cron/interval, s'arrête avec le serveur |
| **requests** (via `http.py`) | appels aux API ouvertes (IGN, Géorisques, DPClim…) | + retry/backoff maison |
| **SQLite** (`registry.py`) | cache d'état | zéro infra, un fichier ; PostgreSQL possible plus tard (seul `registry.py` change) |
| **Leaflet 1.9.4** (vendorisé `web/vendor/`) | carte | continuité v1 ; local → pas de dépendance CDN |
| **Docker Compose** | déploiement | 1 commande, volume persistant |
| **Caddy** (optionnel) | reverse proxy + HTTPS auto | Let's Encrypt sans config |

---

## 4. Le pipeline étape par étape (`pipeline.run`)

| # | Étape | Entrée | Sortie (`data/communes/<INSEE>/processed/`) | Source externe |
|---|---|---|---|---|
| 1 | commune | INSEE | `boundaries/commune*.geojson` | geo.api.gouv.fr |
| 2 | grille | contour L93 | `gabarit.tif` | — |
| 3 | indices | grille | `ndvi.tif`, `ndmi.tif` (+ date S2 dans `metadata.json`) | Copernicus/CDSE |
| 4 | terrain | grille | `slope.tif`, `aspect.tif` | MNT IGN (WMS) |
| 5 | combustible | grille | `fuel.tif` | IGN BD TOPO (WFS) |
| 6 | enjeux | INSEE | `enjeux.tif`, `enjeux_points.geojson` | Géorisques + education.gouv.fr |
| 7 | fwi | centroïde, dept | `fwi.tif` (+ station & date) | Météo-France DPClim |
| 8 | risque | 3-7 | `risk.tif`, `risk_classes.tif`, `priorites.geojson` | — |
| 9 | cog | 3-8 | `*.cog.tif` (9 couches, servies en tuiles) | — |

**Idempotence** : chaque étape est sautée si sa sortie existe *et* n'est pas plus
ancienne que ses entrées (`_outdated`). Une génération qui échoue en cours de
route reprend là où elle s'était arrêtée. Un rafraîchissement supprime seulement
le raster source périmé (`fwi.tif` ou `ndvi/ndmi`) → l'aval se recalcule seul,
les fichiers servis sont écrasés *en place* (pas de 404 pendant l'opération).

---

## 5. Données externes

| Donnée | API | Clé ? | Quota | Où dans le code |
|---|---|---|---|---|
| Contour commune | geo.api.gouv.fr | non | — | `ingestion/commune.py` |
| Sentinel-2 (NDVI/NDMI) | Copernicus Data Space (Sentinel Hub) | **oui** `CDSE_CLIENT_ID/SECRET` | large | `ingestion/sentinel2.py`, `freshness.py` |
| MNT (pente/expo) | IGN Géoplateforme WMS | non | usage raisonnable | `ingestion/mnt.py` |
| Végétation | IGN Géoplateforme WFS | non | idem | `ingestion/landcover.py` |
| ICPE/SEVESO | Géorisques | non | — | `ingestion/enjeux.py` |
| Écoles | data.education.gouv.fr | non | — | `ingestion/enjeux.py` |
| Météo (FWI) | Météo-France DPClim | **oui** `METEOFRANCE_API_KEY` | **50 req/min**, `id-departement` obligatoire | `ingestion/fwi.py` |
| Feux historiques | BDIFF (export CSV manuel) | non | — | `ingestion/bdiff.py` |
| Fonds de carte | IGN Géoplateforme WMTS + OSM | non | — | `web/index.html` |

Clés dans `.env` (jamais commité). En conteneur : injectées par `env_file:` du compose.

---

## 6. Dépannage — où aller selon le problème

| Symptôme | Cause probable | Où regarder / agir |
|---|---|---|
| **Carte blanche, aucune tuile** | Leaflet pas chargé, ou tuiles bloquées à opacité 0 | Console navigateur (F12). `web/index.html` : `fadeAnimation:false`, `fitWhenSized`, `map._resetView`. Vérifier `GET /vendor/leaflet-1.9.4/leaflet.js` → 200. |
| **Les couches disparaissent en changeant de fond** | fond de carte au-dessus des couches | `web/index.html` : pane `basemap` (z-index 150 < 200). Déjà corrigé. |
| **Couche visible mais pâle** | opacité trop basse / fond trop chargé | slider d'opacité dans le panneau ; défaut 0.85 dans `loadCommune`. |
| **Génération échoue** | erreur dans une étape | `GET /api/communes/{insee}/status` → champ `erreur` (traceback). Ou `python -c "from firemap import pipeline; pipeline.run('INSEE')"` pour le traceback complet. Logs uvicorn : lignes `[INSEE] ECHEC [etape X]`. |
| **`[etape fwi] IndexError`** | station météo sans humidité/vent | `ingestion/fwi.py` (`nearest_open_stations`) + `departements.py` (`search_batches`). Corrigé : essaie plusieurs stations, élargit à la région. |
| **`ReadTimeout` / `NameResolutionError`** | réseau instable (transitoire) | `http.py` retente automatiquement (3×). Relancer : `POST /api/communes/{insee}/generate`. |
| **Tuile → 404** | COG manquant (échec ou suppression) | régénérer : `POST /api/communes/{insee}/generate?force=true`. |
| **Mauvaise commune / plusieurs résultats** | recherche par nom ambiguë | `ingestion/commune.py` : requête par **code INSEE**. `GET /api/communes/search?q=...` renvoie l'INSEE. |
| **Nom affiché = "Solliès-Pont" pour une autre commune** | `?insee=X` sans `&nom=` | `web/index.html` : `updateFreshness` lit `meta.commune`. Corrigé. |
| **BDIFF : accents cassés / colonnes décalées** | encodage / séparateur | `ingestion/bdiff.py` : `sep=";"`, UTF-8 (repli cp1252), `skiprows=3`. Ne pas ré-enregistrer le CSV via Excel. |
| **Validation BDIFF « non concluante »** | commune avec trop peu de feux (normal) | `scripts/phase5_validation.py` : garde `if not results`. Ce n'est pas une erreur. |
| **Quota Météo-France (429)** | trop d'appels rapprochés | `http.py` gère le 429 (retry backoff). En masse : espacer / réduire `_MAX_PARALLEL`. |
| **Rafraîchissement automatique ne se fait pas** | serveur arrêté, ou pas encore l'heure | `scheduler.py` tourne **dans le serveur**, toutes les 12 h. Forcer : `GET /api/refresh/scan`. Log `[scheduler] ...` au démarrage. |
| **Commune régénérée mais carte inchangée** | tuiles en cache navigateur | l'URL est versionnée `?v=<genere_le>` ; le poll `/status` (60 s) recharge. Sinon Ctrl-F5. |
| **Statut bloqué sur `running` après redémarrage** | job tué avec le process | `jobs.reset_orphans()` au démarrage les repasse en `error`. Relancer. |
| **`main` import échoue** | dépendance manquante | `pip install -r requirements.txt -r requirements-dev.txt` dans le venv. |
| **Tests rouges** | régression logique | `python -m pytest -v` ; les tests pointent le module concerné (`test_freshness`, `test_registry`…). |
| **Conteneur : `data/priority_communes.json` absent** | volume neuf | Docker recopie le fichier de l'image au 1er `up` (volume **nommé**). Sinon `docker compose cp`. |

---

## 7. Commandes utiles

```bash
# local
.venv\Scripts\python -m uvicorn firemap.api.main:app --port 8000   # serveur
.venv\Scripts\python -m pytest -q                                  # tests
.venv\Scripts\python -c "from firemap import pipeline; pipeline.run('83130', nom='Solliès-Pont')"
.venv\Scripts\python -c "from firemap import registry; [print(e.insee, e.statut, (e.erreur or '')[:120]) for e in registry.list_all()]"
.venv\Scripts\python scripts/loadtest.py --insee 83130 --concurrency 20 --duration 15

# API
curl localhost:8000/api/health
curl localhost:8000/api/communes                       # état de toutes les communes
curl localhost:8000/api/refresh/scan                   # passe de rafraîchissement manuelle
curl -X POST 'localhost:8000/api/communes/83130/generate?force=true'

# déploiement (cf. DEPLOY.md)
docker compose up -d --build
docker compose logs -f
```

---

## 8. Décisions d'architecture (le « pourquoi »)

| Choix | Raison | Alternative écartée |
|---|---|---|
| **Génération à la demande + cache** | 35 000 communes = ~1,5 To / semaines de calcul, inutile (zone feu = sud) | tout précalculer |
| **SQLite pour le registre** | zéro infra, un fichier, charge très faible | PostgreSQL (possible plus tard, `registry.py` isolé) |
| **Worker in-process (ThreadPool 2)** | pipeline IO-bound, pas d'infra externe, délai 15/09 | RQ/Celery + Redis |
| **Planificateur in-process (APScheduler)** | cohérent avec le worker ; `--workers 1` obligatoire | cron externe |
| **COG + rio-tiler** | tuiles à la volée, lecture partielle, cache HTTP `immutable` | pré-générer les PNG (v1) |
| **Idempotence par date de modif** | rafraîchissement sans supprimer les fichiers servis (pas de 404) | supprimer + `force=True` |
| **CommuneContext au lieu d'une globale** | plusieurs communes en parallèle sans collision, testable | `config.COMMUNE_*` (legacy) |
| **Leaflet vendorisé** | le shell de carte ne doit pas dépendre d'un CDN | `unpkg.com` (DNS a échoué en test) |
| **Retry HTTP centralisé (`http.py`)** | les API publiques ont des timeouts passagers fréquents | `requests.get` nu |

---

*Voir aussi : `DEPLOY.md` (mise en ligne), `CAHIER_DES_CHARGES_V2_independant.md` (spécification).*
