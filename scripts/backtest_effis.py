"""Backtest : compare les zones classees a risque (risk_classes.tif) aux
zones REELLEMENT brulees (EFFIS/Copernicus, MODIS burnt-area) pour une
commune deja generee.

Limite assumee (a lire avant les resultats) : compare la classification
ACTUELLE (satellite/meteo d'aujourd'hui) a l'historique des feux passes, pas
une reconstruction du risque tel qu'il etait exactement le jour du feu (ca
demanderait de refaire tourner tout le pipeline avec des fenetres Sentinel-2 /
FWI historiques -- un chantier a part). Comme pente/exposition/combustible/
proximite-enjeux ne changent quasiment pas dans le temps (cf. discussion sur
le rafraichissement), c'est une approximation raisonnable pour une premiere
passe, pas une validation statistique complete.

Usage :
  .venv/Scripts/python.exe scripts/backtest_effis.py 83130 2018 2019 2020 2021 2022 2023 2024
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import rasterio

from firemap.context import CommuneContext
from firemap.grid import ReferenceGrid
from firemap.ingestion.effis import fetch_burnt_mask

RISK_LABELS = {1: "Faible", 2: "Modere", 3: "Eleve", 4: "Tres eleve"}


def main(insee: str, years: list[int]) -> None:
    ctx = CommuneContext(insee)
    classes_path = ctx.processed("risk_classes.tif")
    if not classes_path.exists():
        print(f"Commune {insee} pas encore generee (risk_classes.tif absent) -- "
              f"generer la commune d'abord (pipeline.run).")
        return

    with rasterio.open(classes_path) as src:
        classes = src.read(1)
        grid = ReferenceGrid(transform=src.transform, width=src.width,
                              height=src.height, crs=src.crs, resolution=src.res[0])

    print(f"=== Backtest EFFIS -- commune {insee} ===\n")
    any_burnt = False
    for year in years:
        try:
            burnt = fetch_burnt_mask(year, grid)
        except Exception as exc:
            print(f"{year} : EFFIS indisponible ({type(exc).__name__}: {exc})")
            continue

        n_burnt = int(burnt.sum())
        if n_burnt == 0:
            print(f"{year} : aucune zone brulee (EFFIS) sur cette commune")
            continue

        any_burnt = True
        surface_ha = n_burnt * (grid.resolution ** 2) / 10_000
        hors_commune = int(((classes == 0) & burnt).sum())
        print(f"\n{year} : {n_burnt} pixels brules selon EFFIS (~{surface_ha:.1f} ha)")
        if hors_commune:
            print(f"  (dont {100 * hors_commune / n_burnt:.0f}% hors de la commune -- "
                  f"le feu deborde sur les communes voisines, pourcentages ci-dessous "
                  f"calcules seulement sur la part DANS la commune)")
        in_commune = n_burnt - hors_commune
        for c in (4, 3, 2, 1):
            n = int(((classes == c) & burnt).sum())
            pct = 100 * n / in_commune if in_commune else 0.0
            ecart = pct - 25.0  # 25% = ce qu'on aurait si le risque etait sans rapport avec les feux
            print(f"  {RISK_LABELS[c]:<10} : {pct:5.1f}% de la part brulee DANS la commune "
                  f"({'+' if ecart >= 0 else ''}{ecart:.1f} pt vs 25% attendu si aleatoire)")

    if not any_burnt:
        print("\nAucun feu EFFIS (>= ~30 ha, detection MODIS) trouve sur cette "
              "commune pour les annees testees -- pas de conclusion possible "
              "(ce n'est pas un echec du modele, juste pas de cas de test ici).")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], [int(y) for y in sys.argv[2:]])
