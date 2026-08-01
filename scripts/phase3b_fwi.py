"""
Phase 3 (suite) - FWI / danger meteo
Recupere la station Meteo-France (DPClim) la plus proche du Muy, calcule le
systeme canadien Foret-Meteo (FFMC/DMC/DC/ISI/BUI/FWI) sur ~90 jours de
spin-up, puis produit fwi.tif (raster uniforme sur la commune - le FWI est
une grandeur meteo a grande echelle, ~8-10 km de resolution meme dans les
produits officiels, cf. limite documentee en Phase 7).
"""
import matplotlib.pyplot as plt
import numpy as np
import rasterio

from firemap import config
from firemap.grid import build_reference_grid, rasterize_commune_mask
from firemap.ingestion.commune import load_or_fetch_commune
from firemap.ingestion.fwi import compute_fwi_series, fetch_daily_weather, find_nearest_station


def main():
    print("=== Phase 3 (suite) : FWI / danger meteo ===\n")

    gdf, gdf_l93 = load_or_fetch_commune()
    row = gdf.iloc[0]
    centroid = gdf.geometry.iloc[0].centroid
    print(f"Commune : {row['nom']} (INSEE {row['code']})")

    grid = build_reference_grid(gdf_l93)
    commune_mask = rasterize_commune_mask(gdf_l93, grid).astype(bool)

    station = find_nearest_station(centroid.y, centroid.x, departement="83")
    print(f"Station Meteo-France retenue : {station['nom']} (id {station['id']}), "
          f"a {station['alt']} m d'altitude")

    weather = fetch_daily_weather(station["id"], "2026-05-01T00:00:00Z", "2026-07-31T00:00:00Z")
    print(f"{len(weather)} jours de donnees recuperes ({weather['DATE'].min().date()} "
          f"-> {weather['DATE'].max().date()})")

    fwi_series = compute_fwi_series(weather)
    last = fwi_series.dropna().iloc[-1]
    print(f"\nDernier jour disponible ({last['DATE'].date()}) :")
    print(f"  FFMC={last['FFMC']:.1f}  DMC={last['DMC']:.1f}  DC={last['DC']:.1f}  "
          f"ISI={last['ISI']:.1f}  BUI={last['BUI']:.1f}  FWI={last['FWI']:.1f}")

    current_fwi = float(last["FWI"])

    fwi_raster = np.full((grid.height, grid.width), current_fwi, dtype="float32")
    profile = grid.profile
    with rasterio.open(config.PROCESSED_DIR / "fwi.tif", "w", **profile) as dst:
        dst.write(fwi_raster, 1)
    print(f"\nfwi.tif sauvegarde (valeur uniforme {current_fwi:.1f} sur la grille gabarit) "
          f"-> {config.PROCESSED_DIR / 'fwi.tif'}")

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(fwi_series["DATE"], fwi_series["FWI"], color="firebrick")
    ax.axhline(current_fwi, color="gray", linestyle="--", linewidth=1)
    ax.set_title(f"FWI (indice Foret-Meteo) - station {station['nom']} (mai-juillet 2026)")
    ax.set_xlabel("Date")
    ax.set_ylabel("FWI")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(config.OUTPUTS_DIR / "fwi_timeseries.png", dpi=150)
    print(f"Serie temporelle sauvegardee -> {config.OUTPUTS_DIR / 'fwi_timeseries.png'}")

    print("\n--- Verification alignement grille gabarit ---")
    with rasterio.open(config.PROCESSED_DIR / "fwi.tif") as src:
        ok = (src.crs.to_epsg() == grid.crs.to_epsg() and src.width == grid.width
              and src.height == grid.height and src.transform == grid.transform)
        print(f"fwi.tif : CRS={src.crs.to_epsg()} {src.width}x{src.height} {'OK' if ok else 'DESALIGNE !'}")

    print("\n=== Resultat ===")
    print(f"FWI actuel ({last['DATE'].date()}) : {current_fwi:.1f}")
    print("Hypothese : FWI calcule sur une station unique (pas de reseau dense sur "
          "la commune), applique de facon uniforme - coherent avec la resolution "
          "grossiere du FWI meme dans les produits officiels (~8-10 km).")


if __name__ == "__main__":
    main()
