"""
Phase 4 - Score de risque & priorisation
Normalise et fusionne les couches (Phases 2-3) en un score de risque 0-1,
classe en 4 niveaux, puis croise avec les enjeux pour extraire les zones
prioritaires d'application du retardant.
"""
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.colors import ListedColormap, BoundaryNorm

from firemap import config
from firemap.grid import build_reference_grid, rasterize_commune_mask
from firemap.ingestion.commune import load_or_fetch_commune
from firemap.ingestion.enjeux import fetch_enjeux_points
from firemap.risk.fusion import RISK_LABELS, classify_risk, compute_risk, read_layer
from firemap.risk.priorisation import compute_priorite, extract_priority_zones


def save_raster(path, array, grid, dtype="float32", nodata=None):
    profile = grid.profile
    profile.update(dtype=dtype, nodata=nodata)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array.astype(dtype), 1)


def main():
    print("=== Phase 4 : score de risque & priorisation ===\n")

    gdf, gdf_l93 = load_or_fetch_commune()
    row = gdf.iloc[0]
    code_insee = row["code"]
    print(f"Commune : {row['nom']} (INSEE {code_insee})")

    grid = build_reference_grid(gdf_l93)
    commune_mask = rasterize_commune_mask(gdf_l93, grid).astype(bool)

    # ------------------------------------------------------------------
    # 1) Normalisation + fusion multicritere
    # ------------------------------------------------------------------
    print("\n--- Fusion multicritere ---")
    layers = compute_risk(commune_mask)
    risk = layers["risk"]
    valid_mask = layers["valid_mask"]

    for name in ["secheresse", "fwi", "fuel", "pente", "expo"]:
        vals = layers[name][valid_mask]
        print(f"  {name:12s} moy={vals.mean():.2f}  min={vals.min():.2f}  max={vals.max():.2f}")

    print(f"  risk (fusionne) moy={np.nanmean(risk[valid_mask]):.2f}")
    save_raster(config.PROCESSED_DIR / "risk.tif", np.nan_to_num(risk, nan=0.0), grid, nodata=0.0)
    print(f"risk.tif sauvegarde -> {config.PROCESSED_DIR / 'risk.tif'}")

    # ------------------------------------------------------------------
    # 2) Classification en 4 niveaux (quantiles)
    # ------------------------------------------------------------------
    risk_classes, (q1, q2, q3) = classify_risk(risk, valid_mask)
    print(f"\nSeuils de quantiles : Q1={q1:.3f} Q2={q2:.3f} Q3={q3:.3f}")
    for cls in [1, 2, 3, 4]:
        pct = 100 * (risk_classes[valid_mask] == cls).sum() / valid_mask.sum()
        print(f"  Classe {cls} ({RISK_LABELS[cls]:10s}) : {pct:.1f} % de la commune")
    save_raster(config.PROCESSED_DIR / "risk_classes.tif", risk_classes, grid, dtype="uint8", nodata=0)
    print(f"risk_classes.tif sauvegarde -> {config.PROCESSED_DIR / 'risk_classes.tif'}")

    # ------------------------------------------------------------------
    # 3) Priorisation = risk x proximite enjeux
    # ------------------------------------------------------------------
    print("\n--- Priorisation (risk x proximite enjeux) ---")
    enjeux_distance = read_layer("enjeux.tif")
    priorite = compute_priorite(risk, enjeux_distance, valid_mask)

    enjeux_pts = fetch_enjeux_points(code_insee)
    enjeux_l93 = enjeux_pts.to_crs("EPSG:2154")
    enjeux_l93.to_file(config.PROCESSED_DIR / "enjeux_points.geojson", driver="GeoJSON")

    zones = extract_priority_zones(priorite, risk_classes, valid_mask, grid, enjeux_l93)
    print(f"{len(zones)} zones prioritaires extraites (top 10% du score de priorite)")
    print(zones[["id", "surface_m2", "score_priorite", "classe_risque", "enjeu_proche", "distance_enjeu_m"]]
          .head(10).to_string(index=False))

    zones_wgs84 = zones.to_crs("EPSG:4326")
    priorites_path = config.PROCESSED_DIR / "priorites.geojson"
    zones_wgs84.to_file(priorites_path, driver="GeoJSON")
    print(f"\npriorites.geojson sauvegarde -> {priorites_path}")

    # ------------------------------------------------------------------
    # 4) Visualisation
    # ------------------------------------------------------------------
    display_classes = np.where(commune_mask, risk_classes, np.nan)
    colors = ["#2ca02c", "#f1c40f", "#e67e22", "#c0392b"]  # Faible->Tres eleve
    cmap = ListedColormap(colors)
    norm = BoundaryNorm([0.5, 1.5, 2.5, 3.5, 4.5], cmap.N)

    fig, ax = plt.subplots(figsize=(7, 7))
    im = ax.imshow(display_classes, cmap=cmap, norm=norm)
    zones_grid = zones.copy()
    zones_grid["geometry"] = zones_grid.geometry  # deja en L93 (grid.crs)

    # Superposer les contours des zones prioritaires (conversion coord. -> pixels)
    inv_transform = ~grid.transform
    for geom in zones_grid.geometry:
        polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
        for poly in polys:
            xs, ys = poly.exterior.xy
            cols, rows = inv_transform * (np.array(xs), np.array(ys))
            ax.plot(cols, rows, color="black", linewidth=1.2)

    cbar = fig.colorbar(im, ax=ax, ticks=[1, 2, 3, 4], shrink=0.8)
    cbar.ax.set_yticklabels([RISK_LABELS[c] for c in [1, 2, 3, 4]])
    ax.set_title(f"Risque incendie (4 classes) + zones prioritaires - {row['nom']}")
    ax.set_xlabel("colonnes (x)")
    ax.set_ylabel("lignes (y)")
    fig.tight_layout()
    fig.savefig(config.OUTPUTS_DIR / "risk_map.png", dpi=150)
    print(f"Carte sauvegardee -> {config.OUTPUTS_DIR / 'risk_map.png'}")

    print("\n=== Resultat ===")
    print("risk.tif (4 classes) : OK")
    print(f"Zones prioritaires coherentes visuellement : {len(zones)} zones, "
          f"associees a {zones['enjeu_proche'].nunique()} enjeux distincts")


if __name__ == "__main__":
    main()
