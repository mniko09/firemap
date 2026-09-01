"""freshness.py -- une donnee source plus recente est-elle disponible pour une
commune deja generee ? (utilise par le planificateur, scheduler.py)

Deux sources, deux methodes :
  - Sentinel-2   : interrogation du catalogue Copernicus (STAC) -- y a-t-il une
    acquisition sur l'emprise de la commune plus recente que celle utilisee,
    avec une nebulosite acceptable ?
  - Meteo-France : arithmetique de dates -- DPClim publie toujours J-1, donc une
    date FWI de plus de 2 jours est forcement perimee (en saison, le FWI bouge
    tous les jours).
"""
import datetime as dt

import geopandas as gpd
from sentinelhub import CRS, BBox, DataCollection, SentinelHubCatalog

from .context import CommuneContext
from .ingestion.sentinel2 import build_sh_config

_MAX_CLOUD = 60.0          # au-dela : nouvelle acquisition pas jugee exploitable
_S2_FALLBACK_DAYS = 5      # repli si le catalogue est injoignable (1 cycle de revisite)
_FWI_MAX_AGE_DAYS = 2      # FWI perime si la derniere journee utilisee est plus vieille


def _parse(d: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(d[:10]) if d else None
    except (TypeError, ValueError):
        return None


def fwi_has_newer(date_fwi: str | None, *, today: dt.date | None = None) -> bool:
    """True si le dernier jour FWI utilise date de plus de _FWI_MAX_AGE_DAYS jours
    (ou si aucune date n'est connue)."""
    today = today or dt.date.today()
    d = _parse(date_fwi)
    return d is None or (today - d).days > _FWI_MAX_AGE_DAYS


def sentinel2_has_newer(ctx: CommuneContext, sentinel2_asof: str | None,
                        *, today: dt.date | None = None) -> bool:
    """True s'il existe une acquisition Sentinel-2 L2A sur l'emprise de la commune
    plus recente que `sentinel2_asof`, avec nebulosite < _MAX_CLOUD %.
    Catalogue injoignable -> repli : perime au-dela de _S2_FALLBACK_DAYS jours.
    """
    today = today or dt.date.today()
    since = _parse(sentinel2_asof)
    if since is None:
        return True
    start = since + dt.timedelta(days=1)   # strictement plus recent
    if start > today:
        return False                        # deja a jour aujourd'hui

    try:
        minx, miny, maxx, maxy = gpd.read_file(ctx.boundary("commune.geojson")).total_bounds
        bbox = BBox((minx, miny, maxx, maxy), crs=CRS.WGS84)

        catalog = SentinelHubCatalog(config=build_sh_config())
        results = catalog.search(
            DataCollection.SENTINEL2_L2A,
            bbox=bbox,
            time=(start.isoformat(), today.isoformat()),
            fields={"include": ["properties.datetime", "properties.eo:cloud_cover"], "exclude": []},
        )
        return any(item["properties"].get("eo:cloud_cover", 100) < _MAX_CLOUD for item in results)
    except Exception:
        return (today - since).days >= _S2_FALLBACK_DAYS


def commune_is_stale(ctx: CommuneContext, *, sentinel2_asof: str | None,
                     date_fwi: str | None, today: dt.date | None = None) -> list[str]:
    """Sources perimees pour cette commune : sous-ensemble de {'fwi', 'sentinel2'}.
    Liste vide = commune a jour. Le planificateur s'en sert pour n'invalider que
    les couches concernees. `today` : injectable pour les tests."""
    stale: list[str] = []
    if fwi_has_newer(date_fwi, today=today):
        stale.append("fwi")
    if sentinel2_has_newer(ctx, sentinel2_asof, today=today):
        stale.append("sentinel2")
    return stale
