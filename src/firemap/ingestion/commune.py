"""[1] Contour commune (GeoJSON) - source geo.api.gouv.fr (IGN)."""
import geopandas as gpd
import requests

from .. import config


def fetch_commune_contour(
    code_insee: str = config.COMMUNE_CODE_INSEE,
    nom: str = config.COMMUNE_NOM,
) -> gpd.GeoDataFrame:
    """Contour officiel d'une commune (source geo.api.gouv.fr / IGN).

    On interroge par CODE INSEE en priorite : un code identifie UNE seule
    commune. La recherche par nom est floue et renverrait par ex.
    "Solliès-Pont", "Solliès-Ville" et "Solliès-Toucas" melangees ; le nom
    ne sert donc que de repli si aucun code INSEE n'est fourni.
    """
    url = "https://geo.api.gouv.fr/communes"
    champs = {
        "fields": "nom,code,contour,centre,surface",
        "format": "geojson",
        "geometry": "contour",
    }
    # code -> 1 resultat exact ; nom -> recherche approximative (repli).
    params = {"code": code_insee, **champs} if code_insee else {"nom": nom, **champs}

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    fc = resp.json()
    if not fc.get("features"):
        raise RuntimeError(f"Aucune commune trouvee pour '{code_insee or nom}'")

    gdf = gpd.GeoDataFrame.from_features(fc["features"], crs="EPSG:4326")
    # Filtre par code -> 1 seule ligne attendue ; on tronque par securite.
    return gdf.iloc[[0]] if code_insee else gdf


def load_or_fetch_commune(force_refresh: bool = False, ctx=None) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Retourne (contour WGS84, contour Lambert-93), depuis le cache local si dispo.

    ctx : firemap.context.CommuneContext optionnel (v2).
      - fourni  -> fichiers isoles sous data/communes/<INSEE>/boundaries/, et
        commune resolue par le code INSEE du contexte ;
      - absent  -> comportement legacy (data/boundaries/ + commune de config.py),
        pour les scripts scripts/phase*.py de la Phase 0.
    """
    if ctx is not None:
        boundaries_dir = ctx.boundaries_dir
        boundaries_dir.mkdir(parents=True, exist_ok=True)
        code_insee, nom = ctx.insee, ctx.nom or ""
    else:
        boundaries_dir = config.BOUNDARIES_DIR
        code_insee, nom = config.COMMUNE_CODE_INSEE, config.COMMUNE_NOM

    wgs84_path = boundaries_dir / "commune.geojson"
    l93_path = boundaries_dir / "commune_l93.geojson"

    if wgs84_path.exists() and l93_path.exists() and not force_refresh:
        return gpd.read_file(wgs84_path), gpd.read_file(l93_path)

    gdf = fetch_commune_contour(code_insee, nom)
    gdf.to_file(wgs84_path, driver="GeoJSON")

    gdf_l93 = gdf.to_crs(config.CRS_COMPUTE)
    gdf_l93.to_file(l93_path, driver="GeoJSON")

    return gdf, gdf_l93
