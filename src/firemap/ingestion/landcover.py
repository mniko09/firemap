"""[4] Occupation du sol / combustible - BD TOPO "zone_de_vegetation" (IGN, WFS
Geoplateforme, sans cle API). Chaque type de vegetation recoit un poids de
sensibilite au feu (0=incombustible, 1=tres inflammable) - hypothese a dire
d'expert, a ajuster avec Brault (cf. Phase 4).
"""
import geopandas as gpd
import requests

WFS_URL = "https://data.geopf.fr/wfs/ows"
LAYER = "BDTOPO_V3:zone_de_vegetation"
PAGE_SIZE = 3000

# Poids de sensibilite au combustible par type de vegetation (BD TOPO "nature").
# Le sol non couvert par cette couche (urbain, eau, roche, culture non listee) recoit 0.
FUEL_WEIGHTS = {
    "Lande ligneuse": 0.85,           # garrigue/maquis, tres inflammable
    "Forêt fermée de conifères": 0.90,  # résineux, tres inflammable
    "Forêt fermée mixte": 0.70,
    "Forêt ouverte": 0.60,
    "Bois": 0.60,
    "Forêt fermée de feuillus": 0.45,
    "Haie": 0.35,
    "Verger": 0.20,
    "Vigne": 0.10,                    # peu combustible, joue souvent un role de pare-feu
}
DEFAULT_WEIGHT = 0.0


def fetch_vegetation_zones(bbox_l93) -> gpd.GeoDataFrame:
    """Recupere toutes les zones de vegetation IGN sur l'emprise donnee (pagine)."""
    minx, miny, maxx, maxy = bbox_l93
    bbox_str = f"{minx},{miny},{maxx},{maxy},EPSG:2154"

    all_features = []
    start_index = 0
    while True:
        params = {
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": LAYER,
            "SRSNAME": "EPSG:2154",
            "BBOX": bbox_str,
            "OUTPUTFORMAT": "application/json",
            "COUNT": PAGE_SIZE,
            "STARTINDEX": start_index,
        }
        resp = requests.get(WFS_URL, params=params, timeout=60)
        resp.raise_for_status()
        fc = resp.json()
        features = fc.get("features", [])
        all_features.extend(features)
        if len(features) < PAGE_SIZE:
            break
        start_index += PAGE_SIZE

    if not all_features:
        return gpd.GeoDataFrame(columns=["nature", "geometry"], crs="EPSG:2154")

    return gpd.GeoDataFrame.from_features(all_features, crs="EPSG:2154")


def assign_fuel_weight(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    gdf = gdf.copy()
    gdf["poids_combustible"] = gdf["nature"].map(FUEL_WEIGHTS).fillna(DEFAULT_WEIGHT)
    return gdf
