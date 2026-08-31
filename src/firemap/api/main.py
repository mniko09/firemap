"""[E] API FastAPI.

v2 (Phase 1) -- gestion multi-communes, sans blocage :
  GET  /api/communes/search?q=...        recherche nom -> INSEE
  GET  /api/communes/{insee}/status      etat + fraicheur (lit le registre)
  POST /api/communes/{insee}/generate    met une generation en file (tache de fond)

Legacy (Phase 0, mono-commune, sera remplace au bloc 5) :
  /api/layers, /api/layers/{id}.png, /api/bounds, /api/metadata, /api/priorites, /api/commune
"""
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import config, jobs, registry
from ..grid import build_reference_grid, rasterize_commune_mask
from ..ingestion.commune import load_or_fetch_commune
from ..storage import LAYERS, compute_wgs84_bounds, export_layer_png
from .routes_communes import router as communes_router

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
    # v2 : registre pret + reprise des jobs orphelins (process precedent tue)
    registry.init_db()
    n_orphans = jobs.reset_orphans()
    if n_orphans:
        print(f"[startup] {n_orphans} generation(s) orpheline(s) repassee(s) en 'error'")

    # legacy : pre-rendu des PNG mono-commune. Non bloquant : si ca casse, les
    # routes v2 doivent quand meme demarrer (le bloc 5 retirera cette partie).
    try:
        _prepare_layers()
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] _prepare_layers ignore ({type(exc).__name__}: {exc})")
    yield


app = FastAPI(title="FIREMAP API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)
app.include_router(communes_router)


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
