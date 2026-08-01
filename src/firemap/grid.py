"""[A] Grille gabarit (grille de reference) et alignement des rasters.

Regle d'or du projet : tous les rasters (ndvi, ndmi, fwi, slope, aspect,
fuel, enjeux) doivent partager la meme emprise, resolution et projection
avant fusion. Ce module definit cette grille une fois pour la commune et
fournit `align_to_grid` pour y reprojeter/reechantillonner n'importe quelle
source (utilise en Phases 2-3).
"""
from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import rasterio
from rasterio import features
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject

from . import config


@dataclass
class ReferenceGrid:
    transform: rasterio.Affine
    width: int
    height: int
    crs: CRS
    resolution: float

    @property
    def profile(self) -> dict:
        return {
            "driver": "GTiff",
            "dtype": "float32",
            "count": 1,
            "crs": self.crs,
            "transform": self.transform,
            "width": self.width,
            "height": self.height,
            "nodata": np.nan,
        }


def build_reference_grid(
    commune_gdf_l93: gpd.GeoDataFrame, resolution: float = config.RESOLUTION
) -> ReferenceGrid:
    """Emprise de la commune (Lambert-93), arrondie au multiple de `resolution`
    le plus proche, pour un alignement pixel-parfait entre toutes les couches."""
    minx, miny, maxx, maxy = commune_gdf_l93.total_bounds

    minx = np.floor(minx / resolution) * resolution
    miny = np.floor(miny / resolution) * resolution
    maxx = np.ceil(maxx / resolution) * resolution
    maxy = np.ceil(maxy / resolution) * resolution

    width = int(round((maxx - minx) / resolution))
    height = int(round((maxy - miny) / resolution))
    transform = from_origin(minx, maxy, resolution, resolution)

    return ReferenceGrid(
        transform=transform,
        width=width,
        height=height,
        crs=CRS.from_epsg(2154),
        resolution=resolution,
    )


def rasterize_commune_mask(commune_gdf_l93: gpd.GeoDataFrame, grid: ReferenceGrid) -> np.ndarray:
    """Masque (1 = pixel dans la commune, 0 = hors commune) sur la grille gabarit."""
    shapes = [(geom, 1) for geom in commune_gdf_l93.geometry]
    return features.rasterize(
        shapes,
        out_shape=(grid.height, grid.width),
        transform=grid.transform,
        fill=0,
        dtype="uint8",
    )


def save_gabarit(path, grid: ReferenceGrid, mask: np.ndarray) -> None:
    profile = grid.profile
    profile.update(dtype="uint8", nodata=0)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(mask, 1)


def align_to_grid(
    src_path, dst_path, grid: ReferenceGrid, resampling: Resampling = Resampling.bilinear
) -> None:
    """Reprojette/reechantillonne un raster source sur la grille gabarit.
    A appliquer a chaque couche brute (Sentinel-2, MNT, occupation du sol,
    FWI, enjeux) avant toute fusion (Phase 4)."""
    with rasterio.open(src_path) as src:
        dst_profile = grid.profile
        dst_profile.update(count=src.count, dtype=src.dtypes[0])
        with rasterio.open(dst_path, "w", **dst_profile) as dst:
            for band in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, band),
                    destination=rasterio.band(dst, band),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=grid.transform,
                    dst_crs=grid.crs,
                    resampling=resampling,
                )
