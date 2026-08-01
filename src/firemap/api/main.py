"""[E] API FastAPI servant les couches a la carte Leaflet ([F]) :
/api/layers (liste), /api/layers/{id}.png (rasters), /api/bounds,
/api/priorites, /api/commune.
"""
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import config
from ..grid import build_reference_grid, rasterize_commune_mask
from ..ingestion.commune import load_or_fetch_commune
from ..storage import LAYERS, compute_wgs84_bounds, export_layer_png

LAYERS_DIR = config.OUTPUTS_DIR / "layers"
BOUNDS_JSON = config.OUTPUTS_DIR / "layers_bounds.json"


def _prepare_layers() -> None:
    LAYERS_DIR.mkdir(parents=True, exist_ok=True)
    _, gdf_l93 = load_or_fetch_commune()
    grid = build_reference_grid(gdf_l93)
    commune_mask = rasterize_commune_mask(gdf_l93, grid).astype(bool)

    if not BOUNDS_JSON.exists():
        BOUNDS_JSON.write_text(json.dumps(compute_wgs84_bounds(grid)))

    for spec in LAYERS:
        png_path = LAYERS_DIR / f"{spec.id}.png"
        src_path = config.PROCESSED_DIR / spec.filename
        if not png_path.exists() and src_path.exists():
            export_layer_png(spec, src_path, png_path, commune_mask)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _prepare_layers()
    yield


app = FastAPI(title="FIREMAP API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


@app.get("/api/layers")
def list_layers():
    return [
        {"id": s.id, "label": s.label, "unit": s.unit, "categorical": s.categorical, "default_on": s.default_on}
        for s in LAYERS
    ]


@app.get("/api/layers/{layer_id}.png")
def get_layer_png(layer_id: str):
    png_path = LAYERS_DIR / f"{layer_id}.png"
    if not png_path.exists():
        raise HTTPException(status_code=404, detail=f"Couche '{layer_id}' introuvable")
    return FileResponse(png_path, media_type="image/png")


@app.get("/api/bounds")
def get_bounds():
    return JSONResponse(json.loads(BOUNDS_JSON.read_text()))


@app.get("/api/metadata")
def get_metadata():
    return FileResponse(config.PROCESSED_DIR / "metadata.json", media_type="application/json")


@app.get("/api/priorites")
def get_priorites():
    return FileResponse(config.PROCESSED_DIR / "priorites.geojson", media_type="application/json")


@app.get("/api/commune")
def get_commune():
    return FileResponse(config.BOUNDARIES_DIR / "commune.geojson", media_type="application/json")


# Sert le frontend statique (web/index.html) en dernier, comme fallback des
# routes /api/* definies ci-dessus.
app.mount("/", StaticFiles(directory=str(config.ROOT_DIR / "web"), html=True), name="web")
