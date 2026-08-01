"""[1] Contour commune (GeoJSON) - source geo.api.gouv.fr (IGN)."""
import geopandas as gpd
import requests

from .. import config


def fetch_commune_contour(nom: str = config.COMMUNE_NOM) -> gpd.GeoDataFrame:
    url = "https://geo.api.gouv.fr/communes"
    params = {
        "nom": nom,
        "fields": "nom,code,contour,centre,surface",
        "format": "geojson",
        "geometry": "contour",
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    fc = resp.json()
    if not fc.get("features"):
        raise RuntimeError(f"Aucune commune trouvee pour '{nom}'")
    return gpd.GeoDataFrame.from_features(fc["features"], crs="EPSG:4326")


def load_or_fetch_commune(force_refresh: bool = False) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Retourne (contour WGS84, contour Lambert-93), depuis le cache local si dispo."""
    wgs84_path = config.BOUNDARIES_DIR / "commune.geojson"
    l93_path = config.BOUNDARIES_DIR / "commune_l93.geojson"

    if wgs84_path.exists() and l93_path.exists() and not force_refresh:
        return gpd.read_file(wgs84_path), gpd.read_file(l93_path)

    gdf = fetch_commune_contour()
    gdf.to_file(wgs84_path, driver="GeoJSON")

    gdf_l93 = gdf.to_crs(config.CRS_COMPUTE)
    gdf_l93.to_file(l93_path, driver="GeoJSON")

    return gdf, gdf_l93
