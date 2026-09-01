"""[3] MNT IGN (RGE ALTI, via WMS-Raster Geoplateforme) - pente et exposition.
Aucune cle API necessaire (service ouvert data.geopf.fr).
"""
from typing import Tuple

import numpy as np
from rasterio.transform import array_bounds
from scipy.ndimage import uniform_filter

from ..grid import ReferenceGrid
from ..http import SESSION

WMS_R_URL = "https://data.geopf.fr/wms-r/wms"
LAYER = "ELEVATION.ELEVATIONGRIDCOVERAGE"


def fetch_elevation(grid: ReferenceGrid) -> np.ndarray:
    """Telecharge le MNT (format BIL 32 bits flottant) directement sur la grille gabarit."""
    left, bottom, right, top = array_bounds(grid.height, grid.width, grid.transform)
    params = {
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetMap",
        "LAYERS": LAYER,
        "STYLES": "",
        "FORMAT": "image/x-bil;bits=32",
        "CRS": f"EPSG:{grid.crs.to_epsg()}",
        "BBOX": f"{left},{bottom},{right},{top}",
        "WIDTH": grid.width,
        "HEIGHT": grid.height,
    }
    resp = SESSION.get(WMS_R_URL, params=params, timeout=(10, 90))
    resp.raise_for_status()
    elevation = np.frombuffer(resp.content, dtype="<f4").reshape(grid.height, grid.width)
    return elevation.astype("float32").copy()


def compute_slope_aspect(elevation: np.ndarray, resolution: float) -> Tuple[np.ndarray, np.ndarray]:
    """Pente (degres) et exposition/aspect (degres, 0=Nord, 90=Est, 180=Sud, 270=Ouest),
    par differences finies (numpy). Aspect = direction vers laquelle le versant est tourne.
    Le MNT est lisse (filtre moyenneur 3x3) avant derivation : a 10 m de resolution,
    le bruit pixel-a-pixel du LIDAR produit sinon une exposition tres bruitee,
    en particulier sur les zones plates."""
    elevation = uniform_filter(elevation, size=3, mode="nearest")
    dz_drow, dz_dcol = np.gradient(elevation, resolution)
    dz_dx = dz_dcol          # positif = altitude croissante vers l'Est
    dz_dnorth = -dz_drow     # positif = altitude croissante vers le Nord (ligne 0 = Nord)

    slope = np.degrees(np.arctan(np.sqrt(dz_dx**2 + dz_dnorth**2)))

    uphill_bearing = np.degrees(np.arctan2(dz_dx, dz_dnorth)) % 360
    aspect = (uphill_bearing + 180) % 360  # direction vers laquelle la pente descend

    return slope.astype("float32"), aspect.astype("float32")
