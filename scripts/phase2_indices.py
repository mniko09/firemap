"""
Phase 2 - Indices satellite (Sentinel-2 via CDSE)
Telecharge NDVI/NDMI sur la commune, masque les nuages (SCL), sauvegarde
ndvi.tif / ndmi.tif alignes sur la grille gabarit, et les visualise en
fausses couleurs.
"""
import numpy as np
import matplotlib.pyplot as plt
import rasterio

from firemap import config
from firemap.grid import build_reference_grid, rasterize_commune_mask
from firemap.ingestion.commune import load_or_fetch_commune
from firemap.ingestion.sentinel2 import fetch_ndvi_ndmi

# Fenetres temporelles candidates (recent -> plus large), pour trouver une
# mosaique la moins nuageuse possible. Aujourd'hui : 2026-07-31.
TIME_WINDOWS = [
    ("2026-07-01", "2026-07-30"),
    ("2026-06-01", "2026-07-30"),
    ("2026-05-01", "2026-07-30"),
]

COVERAGE_TARGET = 90.0  # % de pixels valides (sans nuage) vises sur la commune


def save_raster(path, array, grid):
    profile = grid.profile  # dtype=float32, nodata=nan
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array, 1)


def plot_index(array, commune_mask, title, cmap, path, vmin=-1, vmax=1):
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
    print("=== Phase 2 : indices satellite (NDVI/NDMI) ===\n")

    gdf, gdf_l93 = load_or_fetch_commune()
    row = gdf.iloc[0]
    print(f"Commune : {row['nom']} (INSEE {row['code']})")

    grid = build_reference_grid(gdf_l93)
    commune_mask = rasterize_commune_mask(gdf_l93, grid).astype(bool)
    n_commune_px = commune_mask.sum()

    best_data, best_window, best_coverage = None, None, -1.0
    for window in TIME_WINDOWS:
        print(f"\nTentative fenetre {window} (mosaique la moins nuageuse)...")
        data = fetch_ndvi_ndmi(grid, window)
        valid = data[:, :, 2].astype(bool) & commune_mask
        coverage = 100.0 * valid.sum() / n_commune_px
        print(f"  Couverture valide (sans nuage) sur la commune : {coverage:.1f} %")

        if coverage > best_coverage:
            best_data, best_window, best_coverage = data, window, coverage
        if coverage >= COVERAGE_TARGET:
            break

    print(f"\nFenetre retenue : {best_window} (couverture {best_coverage:.1f} %)")

    ndvi = best_data[:, :, 0]
    ndmi = best_data[:, :, 1]
    valid = best_data[:, :, 2].astype(bool)

    ndvi_masked = np.where(valid, ndvi, np.nan).astype("float32")
    ndmi_masked = np.where(valid, ndmi, np.nan).astype("float32")

    ndvi_path = config.PROCESSED_DIR / "ndvi.tif"
    ndmi_path = config.PROCESSED_DIR / "ndmi.tif"
    save_raster(ndvi_path, ndvi_masked, grid)
    save_raster(ndmi_path, ndmi_masked, grid)
    print(f"\nndvi.tif sauvegarde -> {ndvi_path}")
    print(f"ndmi.tif sauvegarde -> {ndmi_path}")

    ndvi_commune = ndvi_masked[commune_mask]
    ndmi_commune = ndmi_masked[commune_mask]
    print(f"\nNDVI sur la commune : min={np.nanmin(ndvi_commune):.2f} "
          f"moy={np.nanmean(ndvi_commune):.2f} max={np.nanmax(ndvi_commune):.2f}")
    print(f"NDMI sur la commune : min={np.nanmin(ndmi_commune):.2f} "
          f"moy={np.nanmean(ndmi_commune):.2f} max={np.nanmax(ndmi_commune):.2f}")

    plot_index(
        ndvi_masked, commune_mask,
        f"NDVI - {row['nom']} ({best_window[0]} a {best_window[1]})",
        "RdYlGn", config.OUTPUTS_DIR / "ndvi.png",
    )
    plot_index(
        ndmi_masked, commune_mask,
        f"NDMI - {row['nom']} ({best_window[0]} a {best_window[1]})",
        "BrBG", config.OUTPUTS_DIR / "ndmi.png",
    )
    print(f"Visualisations sauvegardees -> {config.OUTPUTS_DIR / 'ndvi.png'}, "
          f"{config.OUTPUTS_DIR / 'ndmi.png'}")

    print("\n=== Resultat ===")
    ok = best_coverage >= COVERAGE_TARGET
    print(f"Couverture sans nuage sur la commune : {best_coverage:.1f} % "
          f"({'OK, pas de trous nuageux majeurs' if ok else 'ATTENTION, trous nuageux significatifs'})")


if __name__ == "__main__":
    main()
