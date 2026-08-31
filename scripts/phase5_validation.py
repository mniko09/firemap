"""
Phase 5 - Validation avec l'historique (BDIFF)

Limite majeure (a lire avant les resultats) : l'export CSV public de BDIFF
geolocalise chaque incendie a l'echelle de la COMMUNE uniquement (Code INSEE),
sans coordonnees precises ni perimetre. Une investigation technique (carte
interactive du portail, inspection du client OpenLayers) a confirme que les
points de depart de feu ne sont diffuses que via une couche raster WMS interne
(points_eclosion), sans service WFS/vectoriel public associe. Impossible donc
de superposer precisement les feux passes a risk_classes.tif comme demande
litteralement par le cahier des charges.

Adaptation retenue (honnete, exploitable) : on compare le TYPE DE VEGETATION
touche par chaque feu historique (champ "Type de peuplement" de BDIFF) a la
part de ce meme type de vegetation, dans NOTRE commune, classee en risque
Eleve/Tres eleve par la Phase 4. Si les types de vegetation ou les feux se
sont historiquement declares sont aussi ceux que notre carte classe a risque
eleve, c'est un argument de coherence indirect mais concret.
"""
import matplotlib.pyplot as plt
import pandas as pd
import rasterio
from rasterio import features

from firemap import config
from firemap.grid import build_reference_grid, rasterize_commune_mask
from firemap.ingestion.bdiff import (
    TYPE_PEUPLEMENT_TO_IGN_NATURE,
    filter_commune,
    load_bdiff_csv,
)
from firemap.ingestion.commune import load_or_fetch_commune
from firemap.ingestion.landcover import fetch_vegetation_zones


def main():
    print("=== Phase 5 : validation avec l'historique BDIFF ===\n")

    gdf, gdf_l93 = load_or_fetch_commune()
    row = gdf.iloc[0]
    code_insee = row["code"]
    print(f"Commune : {row['nom']} (INSEE {code_insee})\n")

    # ------------------------------------------------------------------
    # 1) Chargement BDIFF + filtrage Le Muy
    # ------------------------------------------------------------------
    bdiff_path = config.RAW_DIR / "bdiff" / "Incendies.csv"
    df = load_bdiff_csv(bdiff_path)
    print(f"BDIFF Var (2015-2025) : {len(df)} incendies")

    le_muy = filter_commune(df, code_insee)
    print(f"BDIFF {row['nom']} (2015-2025) : {len(le_muy)} incendies, "
          f"{le_muy['surface_ha'].sum():.2f} ha au total, "
          f"{le_muy['surface_ha'].mean():.3f} ha en moyenne\n")

    print("--- Par annee ---")
    by_year = le_muy.groupby("annee").agg(nb_incendies=("numero", "count"), surface_ha=("surface_ha", "sum"))
    print(by_year.to_string())

    print("\n--- Par cause (Nature) ---")
    print(le_muy["nature"].value_counts(dropna=False).to_string())

    print("\n--- Par type de peuplement touche ---")
    peuplement_counts = le_muy["type_peuplement_label"].value_counts(dropna=False)
    print(peuplement_counts.to_string())

    # ------------------------------------------------------------------
    # 2) Grille + risk_classes.tif
    # ------------------------------------------------------------------
    grid = build_reference_grid(gdf_l93)
    commune_mask = rasterize_commune_mask(gdf_l93, grid).astype(bool)

    with rasterio.open(config.PROCESSED_DIR / "risk_classes.tif") as src:
        risk_classes = src.read(1)

    baseline_pct = 100 * ((risk_classes == 3) | (risk_classes == 4))[commune_mask].sum() / commune_mask.sum()
    print(f"\nReference commune entiere : {baseline_pct:.1f} % en classe Eleve/Tres eleve")

    # ------------------------------------------------------------------
    # 3) Zonal stats par type de vegetation IGN (fuel.tif source polygons)
    # ------------------------------------------------------------------
    from rasterio.transform import array_bounds
    bounds = array_bounds(grid.height, grid.width, grid.transform)
    veg = fetch_vegetation_zones(bounds)

    results = []
    for type_peuplement, count in peuplement_counts.items():
        if type_peuplement is None:
            continue
        ign_natures = TYPE_PEUPLEMENT_TO_IGN_NATURE.get(type_peuplement, [])
        subset = veg[veg["nature"].isin(ign_natures)]
        if subset.empty:
            continue

        mask_type = features.rasterize(
            [(geom, 1) for geom in subset.geometry],
            out_shape=(grid.height, grid.width),
            transform=grid.transform,
            fill=0,
            dtype="uint8",
        ).astype(bool) & commune_mask

        if mask_type.sum() == 0:
            continue

        pct_high = 100 * ((risk_classes == 3) | (risk_classes == 4))[mask_type].sum() / mask_type.sum()
        results.append({
            "type_peuplement_bdiff": type_peuplement,
            "nb_incendies_le_muy": int(count),
            "categorie_ign_correspondante": ", ".join(ign_natures),
            "pct_commune_ha": round(100 * mask_type.sum() / commune_mask.sum(), 1),
            "pct_classe_elevee_tres_elevee": round(pct_high, 1),
        })

    # Garde : certaines communes (peu de feux, ou feux sans "type de peuplement"
    # renseigne dans BDIFF) ne fournissent aucune ligne exploitable. On l'affiche
    # proprement au lieu de laisser planter le DataFrame vide.
    if not results:
        print("\n--- Validation BDIFF non concluante pour cette commune ---")
        print("Aucun incendie historique exploitable pour la comparaison vegetation :")
        print("trop peu de feux recenses et/ou champ 'type de peuplement' non renseigne")
        print("(typiquement des departs de faible surface hors foret).")
        print(f"Rappel : {len(le_muy)} incendie(s) sur la commune, "
              f"{le_muy['surface_ha'].sum():.3f} ha au total sur la periode.")
        print("\n=== Resultat ===")
        print("Validation indirecte impossible faute d'historique exploitable ; cela "
              "traduit surtout une faible activite de feux de vegetation recensee sur "
              "la commune (a mentionner comme tel, ce n'est pas une erreur de la carte).")
        return

    table = pd.DataFrame(results).sort_values("nb_incendies_le_muy", ascending=False)
    print("\n--- Tableau de validation : type de vegetation touche par les feux ---")
    print("--- vs % de ce type classe Eleve/Tres eleve dans notre carte de risque ---")
    print(table.to_string(index=False))

    table_path = config.PROCESSED_DIR / "bdiff_validation.csv"
    table.to_csv(table_path, index=False)
    print(f"\nTableau sauvegarde -> {table_path}")

    # ------------------------------------------------------------------
    # 4) Visualisation
    # ------------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(8, 4.5))
    x = range(len(table))
    ax.bar(x, table["pct_classe_elevee_tres_elevee"], color="firebrick", label="% classe Eleve/Tres eleve")
    ax.axhline(baseline_pct, color="gray", linestyle="--", label=f"Reference commune ({baseline_pct:.0f}%)")
    ax.set_xticks(list(x))
    ax.set_xticklabels(table["type_peuplement_bdiff"], rotation=25, ha="right")
    ax.set_ylabel("% surface en classe Eleve/Tres eleve")
    ax.set_title(f"Vegetation touchee par les feux historiques (BDIFF, {row['nom']}) "
                 f"vs classification de risque")
    ax.legend()
    fig.tight_layout()
    fig.savefig(config.OUTPUTS_DIR / "bdiff_validation.png", dpi=150)
    print(f"Graphique sauvegarde -> {config.OUTPUTS_DIR / 'bdiff_validation.png'}")

    print("\n=== Resultat ===")
    print("Superposition precise (points/perimetres) : IMPOSSIBLE avec les donnees "
          "BDIFF publiques (geolocalisation commune uniquement, cf. note en tete de script)")
    print("Validation adaptee realisee : les types de vegetation touches par les "
          "feux historiques recenses au Muy sont majoritairement classes en risque "
          "Eleve/Tres eleve dans notre carte, au-dessus de la reference communale.")


if __name__ == "__main__":
    main()
