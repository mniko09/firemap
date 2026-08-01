"""[C] Priorisation = risk x proximite_enjeux -> priorites.geojson
(surface, score, enjeu concerne, action recommandee).
"""
from typing import Optional

import geopandas as gpd
import numpy as np
import rasterio
from rasterio import features
from scipy.ndimage import label
from shapely.geometry import shape
from shapely.ops import unary_union

from .fusion import RISK_LABELS, normalize


def compute_priorite(risk: np.ndarray, enjeux_distance: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """priorite = risk x proximite_enjeux, proximite = 1 - normalize(distance)."""
    proximite = 1 - normalize(enjeux_distance, mask)
    priorite = risk * proximite
    return np.where(mask, priorite, np.nan).astype("float32")


def _recommend_action(categorie_enjeu: Optional[str], classe_risque: int) -> str:
    urgence = "prioritaire" if classe_risque >= 4 else "recommandee"
    if categorie_enjeu == "ICPE":
        return (f"Application {urgence} du retardant en lisiere du site industriel/ICPE, "
                f"debroussaillement renforce du perimetre")
    if categorie_enjeu == "Etablissement scolaire":
        return (f"Application {urgence} du retardant en perimetre de securite autour de "
                f"l'etablissement scolaire")
    return f"Application {urgence} du retardant sur la zone (vegetation dense / forte pente / exposition sud)"


def extract_priority_zones(
    priorite: np.ndarray,
    risk_classes: np.ndarray,
    mask: np.ndarray,
    grid,
    enjeux_l93: gpd.GeoDataFrame,
    top_percentile: float = 90.0,
    min_pixels: int = 3,
    max_zones: int = 15,
) -> gpd.GeoDataFrame:
    """Extrait les zones contigues les plus prioritaires (top percentile de `priorite`),
    les associe a l'enjeu le plus proche et propose une action."""
    valid = priorite[mask & ~np.isnan(priorite)]
    threshold = np.percentile(valid, top_percentile)

    is_priority = (mask & (priorite >= threshold)).astype("uint8")
    labeled, n_zones = label(is_priority)

    records = []
    for zone_id in range(1, n_zones + 1):
        blob = labeled == zone_id
        npix = int(blob.sum())
        if npix < min_pixels:
            continue

        area_m2 = npix * (grid.resolution**2)
        mean_score = float(priorite[blob].mean())
        mean_class = int(round(float(risk_classes[blob].mean())))
        mean_class = min(max(mean_class, 1), 4)

        polys = [shape(geom) for geom, _ in features.shapes(blob.astype("uint8"), mask=blob, transform=grid.transform)]
        zone_geom = unary_union(polys)
        centroid = zone_geom.centroid

        if len(enjeux_l93):
            dists = enjeux_l93.geometry.distance(centroid)
            nearest = enjeux_l93.loc[dists.idxmin()]
            enjeu_categorie = nearest["categorie"]
            enjeu_nom = nearest.get("raisonSociale") or nearest.get("nom_etablissement") or enjeu_categorie
            enjeu_distance = float(dists.min())
        else:
            enjeu_categorie, enjeu_nom, enjeu_distance = None, None, None

        records.append(
            {
                "geometry": zone_geom,
                "surface_m2": round(area_m2, 1),
                "score_priorite": round(mean_score, 3),
                "classe_risque": RISK_LABELS[mean_class],
                "enjeu_proche": enjeu_nom,
                "categorie_enjeu": enjeu_categorie,
                "distance_enjeu_m": round(enjeu_distance, 1) if enjeu_distance is not None else None,
                "action_recommandee": _recommend_action(enjeu_categorie, mean_class),
            }
        )

    gdf = gpd.GeoDataFrame(records, crs=grid.crs)
    gdf = gdf.sort_values("score_priorite", ascending=False).head(max_zones).reset_index(drop=True)
    gdf.insert(0, "id", range(1, len(gdf) + 1))
    return gdf
