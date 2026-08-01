"""[B] Fusion multicritere (numpy) : normalisation + ponderation des couches
alignees -> risk.tif (0-1), classe en 4 niveaux (Faible/Modere/Eleve/Tres eleve).
"""
from typing import Dict, Tuple

import numpy as np
import rasterio

from .. import config

# Ponderation (transparente, a ajuster avec Brault - cf. cahier des charges)
WEIGHTS = {
    "secheresse": 0.30,
    "fwi": 0.20,
    "fuel": 0.20,
    "pente": 0.15,
    "expo": 0.15,
}

# FWI calcule sur une station unique (Phase 3) -> raster uniforme. On le
# normalise sur une echelle absolue plutot que min-max spatial (qui serait
# degenere), en s'appuyant sur les seuils EFFIS Europe du Sud (50 = "extreme").
FWI_ABSOLUTE_MAX = 50.0

RISK_LABELS = {1: "Faible", 2: "Modere", 3: "Eleve", 4: "Tres eleve"}


def read_layer(name: str) -> np.ndarray:
    with rasterio.open(config.PROCESSED_DIR / name) as src:
        return src.read(1).astype("float32")


def normalize(array: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Normalisation min-max 0-1, bornes calculees sur les pixels valides (mask)."""
    valid = array[mask & ~np.isnan(array)]
    if valid.size == 0:
        return np.zeros_like(array, dtype="float32")
    vmin, vmax = float(valid.min()), float(valid.max())
    if vmax - vmin < 1e-9:
        return np.full_like(array, 0.5, dtype="float32")
    return np.clip((array - vmin) / (vmax - vmin), 0, 1).astype("float32")


def exposition_score(aspect_deg: np.ndarray) -> np.ndarray:
    """Sud/Sud-Ouest (135-247.5 deg) = 1.0 (sec, tres expose) ; Est/Ouest = 0.5 ; Nord = 0.2."""
    a = aspect_deg % 360
    score = np.full_like(a, 0.5, dtype="float32")
    score[(a >= 135) & (a < 247.5)] = 1.0
    score[(a >= 292.5) | (a < 67.5)] = 0.2
    return score


def compute_risk(commune_mask: np.ndarray) -> Dict[str, np.ndarray]:
    """Calcule les couches normalisees et le risque fusionne (0-1) sur la grille gabarit."""
    ndvi = read_layer("ndvi.tif")
    ndmi = read_layer("ndmi.tif")
    fwi = read_layer("fwi.tif")
    slope = read_layer("slope.tif")
    aspect = read_layer("aspect.tif")
    fuel_weight = read_layer("fuel.tif")

    cloud_mask = ~np.isnan(ndvi) & ~np.isnan(ndmi)
    valid_mask = commune_mask & cloud_mask

    secheresse = 1 - normalize(ndmi, valid_mask)
    fuel_vigor = normalize(ndvi, valid_mask)
    fuel = (fuel_vigor * fuel_weight).astype("float32")
    fwi_norm = np.clip(fwi / FWI_ABSOLUTE_MAX, 0, 1).astype("float32")
    pente = normalize(slope, valid_mask)
    expo = exposition_score(aspect)

    risk = (
        WEIGHTS["secheresse"] * secheresse
        + WEIGHTS["fwi"] * fwi_norm
        + WEIGHTS["fuel"] * fuel
        + WEIGHTS["pente"] * pente
        + WEIGHTS["expo"] * expo
    )
    risk = np.where(valid_mask, risk, np.nan).astype("float32")

    return {
        "secheresse": secheresse,
        "fwi": fwi_norm,
        "fuel": fuel,
        "pente": pente,
        "expo": expo,
        "risk": risk,
        "valid_mask": valid_mask,
    }


def classify_risk(risk: np.ndarray, mask: np.ndarray) -> Tuple[np.ndarray, Tuple[float, float, float]]:
    """4 classes par quantiles (25/50/75%) : 1=Faible, 2=Modere, 3=Eleve, 4=Tres eleve.
    0 = hors commune / donnee invalide."""
    valid = risk[mask & ~np.isnan(risk)]
    q1, q2, q3 = np.percentile(valid, [25, 50, 75])

    classes = np.zeros(risk.shape, dtype="uint8")
    m = mask & ~np.isnan(risk)
    r = risk[m]
    classes[m] = np.where(r <= q1, 1, np.where(r <= q2, 2, np.where(r <= q3, 3, 4)))
    return classes, (float(q1), float(q2), float(q3))
