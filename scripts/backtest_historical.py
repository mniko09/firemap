"""Backtest "equitable" : reconstruit le risque avec les donnees satellite/meteo
D'EPOQUE (juste avant le feu), au lieu de comparer un feu passe a la carte
D'AUJOURD'HUI (limite assumee du premier backtest, scripts/backtest_effis.py).

Reutilise les memes fonctions que le pipeline live (firemap.risk.fusion,
firemap.ingestion.sentinel2 / fwi) -- seule la fenetre temporelle change (se
termine juste avant la date reelle du feu au lieu d'aujourd'hui). Les couches
qui ne varient quasiment pas dans le temps (pente, exposition, combustible --
deja en cache pour la commune) sont copiees telles quelles, pas recalculees.

N'ECRIT RIEN dans data/communes/<INSEE>/processed/ (jamais touche aux fichiers
servis en direct par la plateforme) -- tout va dans data/backtest/<INSEE>/<annee>/.

Usage :
  .venv/Scripts/python.exe scripts/backtest_historical.py 83148 2021
"""
import datetime as dt
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import geopandas as gpd
import numpy as np
import rasterio

from firemap import config
from firemap.context import CommuneContext
from firemap.departements import search_batches
from firemap.grid import ReferenceGrid
from firemap.ingestion.effis import fetch_burnt_mask, fetch_fire_date
from firemap.ingestion.fwi import compute_fwi_series, fetch_daily_weather, nearest_open_stations
from firemap.ingestion.sentinel2 import fetch_ndvi_ndmi
from firemap.risk.fusion import classify_risk, compute_risk

RISK_LABELS = {1: "Faible", 2: "Modere", 3: "Eleve", 4: "Tres eleve"}
_S2_LOOKBACKS_DAYS = (30, 60, 120)   # memes reglages que pipeline.py, juste "fin" != aujourd'hui
_FWI_LOOKBACK_DAYS = 120


def _save(path, array, grid, dtype="float32", nodata=None):
    profile = grid.profile
    profile.update(dtype=dtype, nodata=nodata)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array.astype(dtype), 1)


def _historical_ndvi_ndmi(grid, as_of: dt.date, scratch: Path) -> str:
    """Meme logique que pipeline._step_indices (fenetres 30/60/120j, on garde
    la meilleure couverture sans nuage), mais 'fin de fenetre' = as_of."""
    best = None
    for n in _S2_LOOKBACKS_DAYS:
        window = ((as_of - dt.timedelta(days=n)).isoformat(), as_of.isoformat())
        data = fetch_ndvi_ndmi(grid, window)
        valid = data[:, :, 2].astype(bool)
        coverage = 100.0 * valid.sum() / valid.size
        print(f"  S2 {window} -> {coverage:.1f}% sans nuage")
        if best is None or coverage > best[0]:
            best = (coverage, window, data)
        if coverage >= 90.0:
            break
    _, window, data = best
    valid = data[:, :, 2].astype(bool)
    _save(scratch / "ndvi.tif", np.where(valid, data[:, :, 0], np.nan), grid)
    _save(scratch / "ndmi.tif", np.where(valid, data[:, :, 1], np.nan), grid)
    return window[1]


def _historical_fwi(ctx, grid, centroid, as_of: dt.date, scratch: Path) -> float | None:
    """Meme logique que pipeline._step_fwi (stations proches, on essaie jusqu'a
    en trouver une exploitable), mais fenetre meteo se terminant a as_of."""
    debut = (as_of - dt.timedelta(days=_FWI_LOOKBACK_DAYS)).isoformat() + "T00:00:00Z"
    fin = as_of.isoformat() + "T00:00:00Z"

    for batch in search_batches(ctx.departement):
        for st in nearest_open_stations(centroid.y, centroid.x, batch)[:8]:
            try:
                serie = compute_fwi_series(fetch_daily_weather(st["id"], debut, fin)).dropna()
            except Exception as exc:
                print(f"  station {st['nom']} ignoree ({type(exc).__name__})")
                continue
            if not serie.empty:
                fwi_value = float(serie.iloc[-1]["FWI"])
                print(f"  FWI {fwi_value:.1f} (station {st['nom']}, {serie.iloc[-1]['DATE'].date()})")
                _save(scratch / "fwi.tif", np.full((grid.height, grid.width), fwi_value), grid)
                return fwi_value
    return None


def main(insee: str, year: int) -> None:
    ctx = CommuneContext(insee)
    if not ctx.processed("risk_classes.tif").exists():
        print(f"Commune {insee} pas encore generee.")
        return

    with rasterio.open(ctx.processed("slope.tif")) as src:
        grid = ReferenceGrid(transform=src.transform, width=src.width,
                              height=src.height, crs=src.crs, resolution=src.res[0])
    with rasterio.open(ctx.processed("gabarit.tif")) as src:
        mask = src.read(1).astype(bool)

    print(f"=== Reconstruction historique -- commune {insee}, {year} ===\n")
    burnt = fetch_burnt_mask(year, grid)
    inside = mask & burnt
    if not inside.any():
        print("Aucune zone brulee (EFFIS) sur cette commune pour cette annee.")
        return
    n_burnt = int(inside.sum())
    print(f"{n_burnt} pixels brules dans la commune (~{n_burnt * grid.resolution**2 / 10000:.1f} ha)")

    # Date exacte du feu (via un pixel brule pris au hasard dans la commune)
    rows, cols = np.where(inside)
    r, c = rows[len(rows) // 2], cols[len(cols) // 2]
    x, y = grid.transform * (c + 0.5, r + 0.5)
    from pyproj import Transformer
    lon, lat = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True).transform(x, y)
    firedate_str = fetch_fire_date(year, lon, lat)
    if firedate_str is None:
        print("Date de feu introuvable (GetFeatureInfo vide) -- abandon.")
        return
    firedate = dt.datetime.fromisoformat(firedate_str).date()
    as_of = firedate - dt.timedelta(days=1)
    print(f"Feu declenche le {firedate} -> reconstruction du risque tel qu'il "
          f"etait le {as_of} (donnees satellite/meteo d'epoque)\n")

    # Dossier a part : ne touche jamais data/communes/<insee>/processed/
    scratch = config.DATA_DIR / "backtest" / insee / str(year)
    scratch.mkdir(parents=True, exist_ok=True)
    for name in ("slope.tif", "aspect.tif", "fuel.tif"):
        shutil.copy2(ctx.processed(name), scratch / name)  # ne varient pas dans le temps

    print("Sentinel-2 (NDVI/NDMI) d'epoque :")
    _historical_ndvi_ndmi(grid, as_of, scratch)

    print("\nMeteo (FWI) d'epoque :")
    commune_gdf = gpd.read_file(ctx.boundary("commune.geojson"))
    centroid = commune_gdf.geometry.iloc[0].centroid
    fwi_value = _historical_fwi(ctx, grid, centroid, as_of, scratch)
    if fwi_value is None:
        print("  FWI d'epoque indisponible -- abandon.")
        return

    print("\nRisque reconstruit (donnees d'epoque) :")
    layers = compute_risk(mask, processed_dir=scratch)
    classes, (q1, q2, q3) = classify_risk(layers["risk"], layers["valid_mask"])
    print(f"  quantiles Q1/Q2/Q3 = {q1:.3f}/{q2:.3f}/{q3:.3f}")

    print(f"\n--- Comparaison feu reel vs risque D'EPOQUE (commune {insee}, {firedate}) ---")
    for c in (4, 3, 2, 1):
        n = int(((classes == c) & inside).sum())
        pct = 100 * n / n_burnt
        ecart = pct - 25.0
        print(f"  {RISK_LABELS[c]:<10} : {pct:5.1f}% de la surface brulee "
              f"({'+' if ecart >= 0 else ''}{ecart:.1f} pt vs 25% attendu si aleatoire)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], int(sys.argv[2]))
