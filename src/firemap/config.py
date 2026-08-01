"""Constantes partagees par tous les modules : chemins, CRS, resolution."""
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]

DATA_DIR = ROOT_DIR / "data"
BOUNDARIES_DIR = DATA_DIR / "boundaries"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
OUTPUTS_DIR = ROOT_DIR / "outputs"

for _d in (BOUNDARIES_DIR, RAW_DIR, PROCESSED_DIR, OUTPUTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Convention CRS du projet (cf. cahier des charges) :
# calculs en Lambert-93, affichage web en WGS84.
CRS_COMPUTE = "EPSG:2154"
CRS_WEB = "EPSG:4326"

# Resolution de la grille gabarit, en metres (resolution native Sentinel-2 B04/B08).
RESOLUTION = 10

COMMUNE_NOM = "Le Muy"
