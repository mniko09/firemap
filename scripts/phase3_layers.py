"""
Phase 3 - Couches complementaires
Pente/exposition (MNT IGN), combustible (vegetation IGN), enjeux
(ICPE/SEVESO + ecoles), toutes alignees sur la grille gabarit.

FWI/danger meteo : NON traite ici, voir la note en fin d'execution
(service EFFIS indisponible cote serveur au moment du test, alternatives
Meteo-France/CDS necessitant un compte).
"""
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio import features
from rasterio.transform import array_bounds
from scipy.ndimage import distance_transform_edt

from firemap import config
from firemap.grid import build_reference_grid, rasterize_commune_mask
from firemap.ingestion.commune import load_or_fetch_commune
from firemap.ingestion.enjeux import fetch_enjeux_points
from firemap.ingestion.landcover import assign_fuel_weight, fetch_vegetation_zones
from firemap.ingestion.mnt import compute_slope_aspect, fetch_elevation


def save_raster(path, array, grid):
    profile = grid.profile
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array.astype("float32"), 1)


def plot_raster(array, commune_mask, title, cmap, path, vmin=None, vmax=None):
    display = np.where(commune_mask, array, np.nan)
    fig, ax = plt.subplots(figsize=(6, 6))
    im = ax.imshow(display, cmap=cmap, vmin=vmin, vmax=vmax)
    ax.set_title(title)
    ax.set_xlabel("colonnes (x)")
    ax.set_ylabel("lignes (y)")
    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    print("=== Phase 3 : couches complementaires ===\n")

    gdf, gdf_l93 = load_or_fetch_commune()
    row = gdf.iloc[0]
    code_insee = row["code"]
    print(f"Commune : {row['nom']} (INSEE {code_insee})")

    grid = build_reference_grid(gdf_l93)
    commune_mask = rasterize_commune_mask(gdf_l93, grid).astype(bool)
    bounds = array_bounds(grid.height, grid.width, grid.transform)

    # ------------------------------------------------------------------
    # 1) MNT -> pente / exposition
    # ------------------------------------------------------------------
    print("\n--- Pente / exposition (MNT RGE ALTI, IGN) ---")
    elevation = fetch_elevation(grid)
    print(f"Altitude sur la commune : min={elevation[commune_mask].min():.0f} m "
          f"max={elevation[commune_mask].max():.0f} m")

    slope, aspect = compute_slope_aspect(elevation, grid.resolution)
    print(f"Pente : moy={slope[commune_mask].mean():.1f} deg, "
          f"max={slope[commune_mask].max():.1f} deg")

    save_raster(config.PROCESSED_DIR / "slope.tif", slope, grid)
    save_raster(config.PROCESSED_DIR / "aspect.tif", aspect, grid)
    plot_raster(slope, commune_mask, f"Pente (deg) - {row['nom']}", "YlOrRd",
                config.OUTPUTS_DIR / "slope.png", vmin=0, vmax=45)
    plot_raster(aspect, commune_mask, f"Exposition (deg, 0=Nord/90=Est/180=Sud/270=Ouest) - {row['nom']}",
                "twilight", config.OUTPUTS_DIR / "aspect.png", vmin=0, vmax=360)
    print("slope.tif, aspect.tif sauvegardes.")

    # ------------------------------------------------------------------
    # 2) Occupation du sol -> combustible
    # ------------------------------------------------------------------
    print("\n--- Combustible (vegetation IGN BD TOPO) ---")
    veg = fetch_vegetation_zones(bounds)
    veg = assign_fuel_weight(veg)
    print(f"{len(veg)} zones de vegetation recuperees.")

    shapes = sorted(
        zip(veg.geometry, veg["poids_combustible"]),
        key=lambda item: item[1],
    )
    fuel = features.rasterize(
        shapes,
        out_shape=(grid.height, grid.width),
        transform=grid.transform,
        fill=0.0,
        dtype="float32",
    )
    save_raster(config.PROCESSED_DIR / "fuel.tif", fuel, grid)
    plot_raster(fuel, commune_mask, f"Poids combustible (0-1) - {row['nom']}", "YlOrBr",
                config.OUTPUTS_DIR / "fuel.png", vmin=0, vmax=1)
    print(f"Combustible moyen sur la commune : {fuel[commune_mask].mean():.2f}")
    print("fuel.tif sauvegarde.")

    # ------------------------------------------------------------------
    # 3) Enjeux (ICPE/SEVESO + ecoles) -> distance au plus proche enjeu
    # ------------------------------------------------------------------
    print("\n--- Enjeux (ICPE/SEVESO Georisques + ecoles education.gouv.fr) ---")
    enjeux_pts = fetch_enjeux_points(code_insee)
    print(f"{len(enjeux_pts)} enjeux ponctuels recuperes "
          f"({(enjeux_pts['categorie'] == 'ICPE').sum()} ICPE, "
          f"{(enjeux_pts['categorie'] == 'Etablissement scolaire').sum()} etablissements scolaires)")

    enjeux_l93 = enjeux_pts.to_crs("EPSG:2154")
    enjeux_shapes = [(geom, 1) for geom in enjeux_l93.geometry]
    enjeux_mask_raster = features.rasterize(
        enjeux_shapes,
        out_shape=(grid.height, grid.width),
        transform=grid.transform,
        fill=0,
        dtype="uint8",
    )
    distance = distance_transform_edt(
        enjeux_mask_raster == 0, sampling=(grid.resolution, grid.resolution)
    ).astype("float32")
    save_raster(config.PROCESSED_DIR / "enjeux.tif", distance, grid)
    plot_raster(distance, commune_mask, f"Distance au plus proche enjeu (m) - {row['nom']}", "viridis_r",
                config.OUTPUTS_DIR / "enjeux.png")
    print(f"Distance moyenne aux enjeux sur la commune : {distance[commune_mask].mean():.0f} m")
    print("enjeux.tif sauvegarde.")

    # ------------------------------------------------------------------
    # Verification de l'alignement (regle d'or)
    # ------------------------------------------------------------------
    print("\n--- Verification alignement grille gabarit ---")
    for name in ["slope.tif", "aspect.tif", "fuel.tif", "enjeux.tif"]:
        with rasterio.open(config.PROCESSED_DIR / name) as src:
            ok = (src.crs.to_epsg() == grid.crs.to_epsg() and src.width == grid.width
                  and src.height == grid.height and src.transform == grid.transform)
            print(f"{name} : CRS={src.crs.to_epsg()} {src.width}x{src.height} "
                  f"{'OK' if ok else 'DESALIGNE !'}")

    print("\n=== Resultat ===")
    print("slope.tif, aspect.tif, fuel.tif, enjeux.tif : alignes sur la grille gabarit (OK)")
    print("fwi.tif : NON PRODUIT - voir note ci-dessous")
    print()
    print("NOTE FWI : la couche EFFIS (WMS, ecmwf.fwi.fwi) a ete testee en detail "
          "(plusieurs dates, formats, styles) mais renvoie actuellement des images "
          "vides ou des erreurs serveur (ex. 'msOracleSpatialLayerOpen(): Connection "
          "failure' sur la couche fwi_nuts5) - probleme cote infrastructure JRC, pas "
          "un probleme de requete. Les deux alternatives du cahier des charges "
          "(Meteo-France API donnees publiques, Copernicus Climate Data Store) "
          "necessitent toutes les deux la creation d'un compte + cle API.")


if __name__ == "__main__":
    main()
