"""Routes des couches d'UNE commune (v2) :
  GET /api/communes/{insee}/layers                 liste des couches disponibles
  GET /api/communes/{insee}/layers/{id}/{z}/{x}/{y}.png   tuile COG (rio-tiler)
  GET /api/communes/{insee}/bounds                 bornes WGS84 (pour cadrer la carte)
  GET /api/communes/{insee}/metadata              fiche de provenance (dates)
  GET /api/communes/{insee}/priorites             zones prioritaires (GeoJSON)
  GET /api/communes/{insee}/commune               contour communal (GeoJSON)
  GET /api/communes/{insee}/value?lat=..&lon=..   valeur de CHAQUE couche au point (obj. 3.4)

Le rendu (colormap + plage) vient de firemap.storage.LAYERS : une seule source
de verite pour la symbologie, partagee avec le legacy.
"""
import io
from functools import lru_cache

import numpy as np
import rasterio
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from PIL import Image
from pyproj import Transformer
from rasterio.windows import Window
from rio_tiler.errors import TileOutsideBounds
from rio_tiler.io import Reader

from .. import config
from ..context import CommuneContext
from ..storage import LAYERS

router = APIRouter(prefix="/api/communes/{insee}", tags=["couches"])

_LAYER_BY_ID = {s.id: s for s in LAYERS}
_TO_L93 = Transformer.from_crs(config.CRS_WEB, config.CRS_COMPUTE, always_xy=True)

# Couleurs des 4 classes de risque (0 = hors commune -> transparent).
_RISK_CMAP = {i: (0, 0, 0, 0) for i in range(256)}
_RISK_CMAP.update({1: (44, 160, 44, 255), 2: (241, 196, 15, 255),
                   3: (230, 126, 34, 255), 4: (192, 57, 43, 255)})

# Tuile 256x256 entierement transparente (hors emprise de la commune).
_EMPTY_TILE = io.BytesIO()
Image.new("RGBA", (256, 256), (0, 0, 0, 0)).save(_EMPTY_TILE, format="PNG")
_EMPTY_TILE = _EMPTY_TILE.getvalue()

# Cache (insee, layer_id) -> (min, max) pour les couches sans plage fixe (ex. enjeux),
# afin que la symbologie soit COHERENTE d'une tuile a l'autre.
_range_cache: dict[tuple[str, str], tuple[float, float]] = {}


def _ctx(insee: str) -> CommuneContext:
    ctx = CommuneContext(insee)
    if not ctx.processed("risk.tif").exists():
        raise HTTPException(status_code=404, detail=f"commune {insee} pas encore generee")
    return ctx


@lru_cache(maxsize=64)
def _colormap(name: str) -> dict:
    """Colormap rio-tiler {0..255 -> (r,g,b,a)} a partir d'un nom matplotlib.
    On tente le registre rio-tiler, repli sur matplotlib (couvre twilight, *_r, ...)."""
    try:
        from rio_tiler.colormap import cmap as _riocmap
        return _riocmap.get(name.lower())
    except Exception:
        import matplotlib
        m = matplotlib.colormaps[name]
        return {i: tuple(int(c * 255) for c in m(i / 255.0)) for i in range(256)}


def _range_for(ctx: CommuneContext, spec) -> tuple[float, float]:
    """Plage (vmin, vmax) pour l'etirement des valeurs -> couleurs.
    Fixe si declaree dans LAYERS ; sinon calculee une fois sur le raster complet."""
    if spec.vmin is not None and spec.vmax is not None:
        return float(spec.vmin), float(spec.vmax)
    key = (ctx.insee, spec.id)
    if key not in _range_cache:
        with rasterio.open(ctx.processed(spec.filename)) as src:
            a = src.read(1, masked=True)
        _range_cache[key] = (float(a.min()), float(a.max()))
    return _range_cache[key]


# ---------------------------------------------------------------------------
@router.get("/layers")
def list_layers(insee: str):
    """Couches effectivement disponibles pour cette commune."""
    ctx = _ctx(insee)
    return [
        {"id": s.id, "label": s.label, "unit": s.unit,
         "categorical": s.categorical, "default_on": s.default_on}
        for s in LAYERS if ctx.processed(s.filename).exists()
    ]


@router.get("/layers/{layer_id}/{z}/{x}/{y}.png")
def layer_tile(insee: str, layer_id: str, z: int, x: int, y: int):
    """Une tuile XYZ (256x256) de la couche, lue dans son COG web-optimise."""
    spec = _LAYER_BY_ID.get(layer_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"couche inconnue : {layer_id}")
    ctx = _ctx(insee)
    cog = ctx.processed(spec.filename[:-4] + ".cog.tif")
    if not cog.exists():
        raise HTTPException(status_code=404, detail=f"COG absent pour {layer_id} (regenerer la commune)")

    try:
        with Reader(str(cog)) as src:
            img = src.tile(x, y, z)
    except TileOutsideBounds:
        return Response(content=_EMPTY_TILE, media_type="image/png")

    if spec.categorical:
        png = img.render(img_format="PNG", colormap=_RISK_CMAP)
    else:
        vmin, vmax = _range_for(ctx, spec)
        img.rescale(in_range=((vmin, vmax),))
        png = img.render(img_format="PNG", colormap=_colormap(spec.cmap))
    return Response(content=png, media_type="image/png")


@router.get("/bounds")
def get_bounds(insee: str):
    """Bornes WGS84 du contour communal (pour `map.fitBounds`)."""
    import geopandas as gpd
    ctx = _ctx(insee)
    minx, miny, maxx, maxy = gpd.read_file(ctx.boundary("commune.geojson")).total_bounds
    return {"west": float(minx), "south": float(miny), "east": float(maxx), "north": float(maxy)}


@router.get("/metadata")
def get_metadata(insee: str):
    ctx = _ctx(insee)
    if not ctx.metadata_path.exists():
        raise HTTPException(status_code=404, detail="metadata absente")
    return FileResponse(ctx.metadata_path, media_type="application/json")


@router.get("/priorites")
def get_priorites(insee: str):
    ctx = _ctx(insee)
    return FileResponse(ctx.processed("priorites.geojson"), media_type="application/geo+json")


@router.get("/commune")
def get_commune(insee: str):
    ctx = _ctx(insee)
    return FileResponse(ctx.boundary("commune.geojson"), media_type="application/geo+json")


@router.get("/value")
def pixel_value(insee: str,
               lat: float = Query(..., ge=-90, le=90),
               lon: float = Query(..., ge=-180, le=180)):
    """Valeur BRUTE de chaque couche au point clique (objectif 3.4 du cahier).
    WGS84 (clic Leaflet) -> Lambert-93 -> index pixel -> lecture fenetree (1 px)."""
    ctx = _ctx(insee)
    x, y = _TO_L93.transform(lon, lat)

    # Toutes les couches partagent la meme grille gabarit : on calcule row/col
    # UNE fois, et on verifie d'abord que le point est bien dans la commune.
    with rasterio.open(ctx.processed("gabarit.tif")) as g:
        row, col = g.index(x, y)
        inside = (0 <= row < g.height and 0 <= col < g.width
                  and int(g.read(1, window=Window(col, row, 1, 1))[0, 0]) != 0)

    if not inside:
        return {"lat": lat, "lon": lon, "dans_commune": False, "valeurs": {}}

    values: dict[str, float | None] = {}
    for s in LAYERS:
        tif = ctx.processed(s.filename)
        if not tif.exists():
            continue
        with rasterio.open(tif) as src:
            v = src.read(1, window=Window(col, row, 1, 1))[0, 0]
        values[s.id] = None if np.isnan(v) else round(float(v), 3)

    return {"lat": lat, "lon": lon, "dans_commune": True, "valeurs": values}
