"""Constantes partagees par tous les modules : chemins, CRS, resolution."""
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]

DATA_DIR = ROOT_DIR / "data"

# --- v2 : une arborescence ISOLEE par commune, sous data/communes/<INSEE>/ ---
# C'est le chemin utilise par le pipeline multi-communes (voir
# firemap.context.CommuneContext). Chaque commune y a ses propres
# boundaries/ raw/ processed/ outputs/.
COMMUNES_DIR = DATA_DIR / "communes"

# --- Legacy v1 / Phase 0 : sorties MONO-commune partagees -------------------
# Utilisees uniquement par les scripts scripts/phase*.py (livraison Sollies-Pont).
# Le code v2 ne doit PLUS s'appuyer dessus : il passe par un CommuneContext.
BOUNDARIES_DIR = DATA_DIR / "boundaries"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
OUTPUTS_DIR = ROOT_DIR / "outputs"

for _d in (COMMUNES_DIR, BOUNDARIES_DIR, RAW_DIR, PROCESSED_DIR, OUTPUTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Convention CRS du projet (cf. cahier des charges) :
# calculs en Lambert-93, affichage web en WGS84.
CRS_COMPUTE = "EPSG:2154"
CRS_WEB = "EPSG:4326"

# Resolution de la grille gabarit, en metres (resolution native Sentinel-2 B04/B08).
RESOLUTION = 10

# --- Commune cible (LEGACY : scripts/phase*.py uniquement) ------------------
# v1 : commune figee ici (COMMUNE_NOM = "Le Muy"). Phase 0 : rendue pilotable
# par variable d'environnement pour livrer Solliès-Pont sans reediter le code.
# v2 : REMPLACE par firemap.context.CommuneContext (un objet par requete/job) ;
# ces trois constantes ne servent plus qu'aux scripts de la Phase 0.
#   FIREMAP_COMMUNE_INSEE : code INSEE sur 5 caracteres -> PRIORITAIRE (non ambigu)
#   FIREMAP_COMMUNE_NOM   : nom en clair                -> repli si le code est absent
# Valeurs par defaut = Solliès-Pont (Var), le livrable court terme du cahier v2.
COMMUNE_CODE_INSEE = os.getenv("FIREMAP_COMMUNE_INSEE", "83130")
COMMUNE_NOM = os.getenv("FIREMAP_COMMUNE_NOM", "Solliès-Pont")

# Departement (2 caracteres) derive du code INSEE : sert notamment a cibler les
# stations Meteo-France du bon departement (cf. scripts/phase3b_fwi.py).
# Corse : "2A"/"2B" ; metropole/DOM : 2 chiffres. geo.api.gouv.fr fournit aussi
# ce champ ("codeDepartement") ; on garde ici un simple prefixe, suffisant.
DEPARTEMENT = COMMUNE_CODE_INSEE[:2]
