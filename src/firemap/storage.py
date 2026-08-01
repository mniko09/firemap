"""[D] Stockage : prepare les couches (rasters Lambert-93 dans data/processed/)
pour la consommation web - reprojection en WGS84, masquage a l'emprise
communale et export en PNG RGBA (overlay Leaflet). Toutes les couches
partagent la meme grille gabarit (Phase 1), donc les memes bornes WGS84 une
fois reprojetees.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

import numpy as np
import rasterio
from matplotlib import colormaps
from PIL import Image
from rasterio.warp import Resampling, calculate_default_transform, reproject

from .grid import ReferenceGrid

# Faible -> vert, Modere -> jaune, Eleve -> orange, Tres eleve -> rouge
RISK_COLORS = {
    1: (44, 160, 44, 255),
    2: (241, 196, 15, 255),
    3: (230, 126, 34, 255),
    4: (192, 57, 43, 255),
}


@dataclass
class LayerSpec:
    id: str
    label: str
    filename: str
    unit: str
    categorical: bool = False
    cmap: str = "viridis"
    vmin: Optional[float] = None
    vmax: Optional[float] = None
    default_on: bool = False


LAYERS = [
    LayerSpec("risk_classes", "Risque incendie (4 classes)", "risk_classes.tif", "classe",
              categorical=True, default_on=True),
    LayerSpec("risk", "Score de risque (continu)", "risk.tif", "0-1", cmap="RdYlGn_r", vmin=0, vmax=1),
    LayerSpec("secheresse_ndmi", "Humidite du couvert (NDMI)", "ndmi.tif", "indice", cmap="BrBG", vmin=-1, vmax=1),
    LayerSpec("vigueur_ndvi", "Vigueur vegetation (NDVI)", "ndvi.tif", "indice", cmap="RdYlGn", vmin=-1, vmax=1),
    LayerSpec("fwi", "Danger meteo (FWI)", "fwi.tif", "indice FWI", cmap="YlOrRd", vmin=0, vmax=100),
    LayerSpec("pente", "Pente", "slope.tif", "degres", cmap="YlOrRd", vmin=0, vmax=45),
    LayerSpec("exposition", "Exposition", "aspect.tif", "degres (0=N,180=S)", cmap="twilight", vmin=0, vmax=360),
    LayerSpec("combustible", "Combustible (vegetation)", "fuel.tif", "poids 0-1", cmap="YlOrBr", vmin=0, vmax=1),
    LayerSpec("enjeux", "Distance aux enjeux", "enjeux.tif", "metres", cmap="viridis_r"),
]


def _reproject_to_wgs84(
    array: np.ndarray, src_transform, src_crs, resampling: Resampling
) -> Tuple[np.ndarray, Tuple[float, float, float, float]]:
    dst_crs = "EPSG:4326"
    from rasterio.transform import array_bounds

    src_bounds = array_bounds(array.shape[0], array.shape[1], src_transform)
    transform, width, height = calculate_default_transform(
        src_crs, dst_crs, array.shape[1], array.shape[0], *src_bounds
    )
    dst = np.full((height, width), np.nan, dtype="float32")
    reproject(
        source=array.astype("float32"),
        destination=dst,
        src_transform=src_transform,
        src_crs=src_crs,
        src_nodata=np.nan,
        dst_transform=transform,
        dst_crs=dst_crs,
        dst_nodata=np.nan,
        resampling=resampling,
    )
    west, north = transform * (0, 0)
    east, south = transform * (width, height)
    return dst, (west, south, east, north)


def compute_wgs84_bounds(grid: ReferenceGrid) -> Dict[str, float]:
    """Bornes WGS84 communes a toutes les couches (meme grille gabarit source)."""
    dummy = np.zeros((grid.height, grid.width), dtype="float32")
    _, (west, south, east, north) = _reproject_to_wgs84(dummy, grid.transform, grid.crs, Resampling.nearest)
    return {"west": west, "south": south, "east": east, "north": north}


def export_layer_png(spec: LayerSpec, src_path: Path, dst_png_path: Path, commune_mask: np.ndarray) -> None:
    """Masque a l'emprise communale, reprojette en WGS84 et exporte en PNG RGBA."""
    with rasterio.open(src_path) as src:
        array = src.read(1).astype("float32")
        src_transform, src_crs = src.transform, src.crs

    array = np.where(commune_mask, array, np.nan)
    resampling = Resampling.nearest if spec.categorical else Resampling.bilinear
    reprojected, _ = _reproject_to_wgs84(array, src_transform, src_crs, resampling)

    rgba = np.zeros((*reprojected.shape, 4), dtype="uint8")
    valid = ~np.isnan(reprojected)

    if spec.categorical:
        for cls, color in RISK_COLORS.items():
            rgba[reprojected == cls] = color
    else:
        vmin = spec.vmin if spec.vmin is not None else np.nanmin(reprojected)
        vmax = spec.vmax if spec.vmax is not None else np.nanmax(reprojected)
        norm = np.clip((reprojected - vmin) / (vmax - vmin + 1e-9), 0, 1)
        cmap = colormaps[spec.cmap]
        colored = (cmap(norm) * 255).astype("uint8")
        rgba[valid] = colored[valid]

    Image.fromarray(rgba, mode="RGBA").save(dst_png_path)
