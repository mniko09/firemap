"""[Backtest] EFFIS (Copernicus) -- masque raster des zones REELLEMENT brulees,
pour valider a posteriori le modele de risque contre des feux passes.

Source : couche WMS EFFIS 'modis.ba.poly.<annee>' (MODIS, ~250 m, detecte les
feux a partir d'environ 30 ha -- valide donc les feux de taille notable, pas
les tout petits departs). Libre acces, sans authentification.

Note technique (2026-09) : le service WFS (vecteur, avec dates/surfaces
precises par feu) de ce serveur repondait en erreur (502) ou en timeout au
moment ou ce module a ete ecrit -- verifie a plusieurs reprises, pas un souci
de parametres. On passe donc par WMS (image), qui lui repond, puis on
reprojette/seuille avec rasterio (meme technique que storage.py pour
l'affichage web). Si le WFS redevient disponible, il donnerait des dates de
feu precises au lieu d'une simple annee -- a retenter plus tard.
"""
import io
import re

import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer
from rasterio.transform import array_bounds, from_bounds
from rasterio.warp import Resampling, reproject

from .. import config
from ..http import SESSION

EFFIS_WMS = "https://maps.effis.emergency.copernicus.eu/effis"
_TO_WGS84 = Transformer.from_crs(config.CRS_COMPUTE, config.CRS_WEB, always_xy=True)
_FIREDATE_RE = re.compile(r"<FIREDATE>([^<]+)</FIREDATE>")


def fetch_burnt_mask(year: int, grid) -> np.ndarray:
    """Masque booleen (meme forme que la grille gabarit fournie) : True = pixel
    marque "brule" par EFFIS pour l'annee donnee. `grid` : ReferenceGrid Lambert-93
    de la commune (cf. firemap.grid)."""
    minx, miny, maxx, maxy = array_bounds(grid.height, grid.width, grid.transform)
    lons, lats = zip(*(_TO_WGS84.transform(x, y) for x, y in
                       [(minx, miny), (maxx, miny), (minx, maxy), (maxx, maxy)]))
    west, east, south, north = min(lons), max(lons), min(lats), max(lats)

    resp = SESSION.get(EFFIS_WMS, timeout=(20, 60), params={
        "service": "WMS", "version": "1.1.1", "request": "GetMap",
        "layers": f"modis.ba.poly.{year}", "styles": "",
        "bbox": f"{west},{south},{east},{north}",
        "width": 512, "height": 512, "srs": "EPSG:4326",
        "format": "image/png", "transparent": "true",
    })
    resp.raise_for_status()
    if b"ServiceException" in resp.content[:200]:
        raise RuntimeError(f"EFFIS WMS a refuse la requete : {resp.content[:300]!r}")

    img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
    burnt_wgs84 = (np.array(img)[:, :, 3] > 0).astype("uint8")  # alpha>0 = polygone dessine

    src_transform = from_bounds(west, south, east, north, img.width, img.height)
    dst = np.zeros((grid.height, grid.width), dtype="uint8")
    reproject(
        source=burnt_wgs84, destination=dst,
        src_transform=src_transform, src_crs=config.CRS_WEB,
        dst_transform=grid.transform, dst_crs=grid.crs,
        resampling=Resampling.nearest,
    )
    return dst.astype(bool)


def fetch_fire_date(year: int, lon: float, lat: float) -> str | None:
    """Date de declenchement (GMT, 'YYYY-MM-DD HH:MM:SS') du feu EFFIS le plus
    proche de ce point WGS84, pour cette annee -- None si pas de feu exactement
    a cet endroit. Sert a reconstruire le risque juste AVANT le feu (backtest
    "equitable", cf. scripts/backtest_historical.py) plutot que de comparer a
    la classification d'aujourd'hui.

    Via GetFeatureInfo (WMS) : le GetFeature du WFS de ce serveur repondait en
    erreur/timeout au moment ou ce module a ete ecrit (cf. docstring du module) ;
    GetFeatureInfo, lui, fonctionne et donne les memes attributs (FIREDATE...)."""
    d = 0.01  # ~1 km : une petite fenetre centree sur le point suffit, on
              # interroge un seul pixel dedans, pas besoin de la vraie emprise
    resp = SESSION.get(EFFIS_WMS, timeout=(20, 60), params={
        "service": "WMS", "version": "1.1.1", "request": "GetFeatureInfo",
        "layers": f"modis.ba.poly.{year}", "query_layers": f"modis.ba.poly.{year}",
        "styles": "", "bbox": f"{lon - d},{lat - d},{lon + d},{lat + d}",
        "width": 101, "height": 101, "srs": "EPSG:4326", "x": 50, "y": 50,
        "info_format": "application/vnd.ogc.gml", "feature_count": 1,
    })
    resp.raise_for_status()
    m = _FIREDATE_RE.search(resp.text)
    return m.group(1) if m else None
