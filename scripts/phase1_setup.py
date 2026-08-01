"""
Phase 1 - Architecture technique
Construit/valide l'arborescence du projet et definit le raster gabarit
(grille de reference) pour la commune pilote.
"""
import matplotlib.pyplot as plt

from firemap import config
from firemap.grid import build_reference_grid, rasterize_commune_mask, save_gabarit
from firemap.ingestion.commune import load_or_fetch_commune


def main():
    print("=== Phase 1 : architecture & grille gabarit ===\n")

    gdf, gdf_l93 = load_or_fetch_commune()
    row = gdf.iloc[0]
    print(f"Commune : {row['nom']} (INSEE {row['code']})")

    grid = build_reference_grid(gdf_l93)
    print(f"Grille gabarit : {grid.width} x {grid.height} px, "
          f"resolution {grid.resolution} m, CRS {grid.crs}")
    print(f"Transform (L93) : {grid.transform}")

    mask = rasterize_commune_mask(gdf_l93, grid)
    n_valid = int(mask.sum())
    print(f"Pixels dans la commune : {n_valid} / {mask.size} "
          f"({100 * n_valid / mask.size:.1f} %)")

    gabarit_path = config.PROCESSED_DIR / "gabarit.tif"
    save_gabarit(gabarit_path, grid, mask)
    print(f"Raster gabarit sauvegarde -> {gabarit_path}")

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.imshow(mask, cmap="Oranges")
    ax.set_title(f"Grille gabarit - {row['nom']} ({grid.width}x{grid.height} px @ {grid.resolution} m)")
    ax.set_xlabel("colonnes (x)")
    ax.set_ylabel("lignes (y)")
    fig.tight_layout()
    png_path = config.OUTPUTS_DIR / "gabarit_mask.png"
    fig.savefig(png_path, dpi=150)
    print(f"Visualisation sauvegardee -> {png_path}")

    print("\n=== Resultat ===")
    print("Arborescence projet (module par bloc) : OK")
    print("Raster gabarit defini pour la commune : OK")


if __name__ == "__main__":
    main()
