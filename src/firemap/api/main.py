"""API FastAPI -- FIREMAP v2, gestion multi-communes.

  GET  /api/health                         liveness (registre joignable)
  GET  /api/communes                       etat de TOUTES les communes (exploitation)
  GET  /api/communes/search?q=...          recherche nom -> INSEE
  GET  /api/communes/{insee}/status        etat + fraicheur d'une commune
  POST /api/communes/{insee}/generate      met une generation en file (tache de fond)
  GET  /api/communes/{insee}/layers|bounds|metadata|priorites|commune|value|layers/{id}/{z}/{x}/{y}.png
  GET  /api/refresh/scan                   declenche une passe de rafraichissement
  GET  /                                   frontend statique (web/)
"""
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .. import config, jobs, registry, scheduler
from .routes_communes import router as communes_router
from .routes_layers import router as layers_router

try:
    __version__ = _pkg_version("firemap")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    registry.init_db()
    n_orphans = jobs.reset_orphans()          # generations restees 'running' apres un redemarrage
    if n_orphans:
        print(f"[startup] {n_orphans} generation(s) orpheline(s) repassee(s) en 'error'", flush=True)
    scheduler.start_scheduler()               # rafraichissement automatique (Phase 3)
    yield
    scheduler.stop_scheduler()


app = FastAPI(title="FIREMAP API", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)
app.include_router(communes_router)
app.include_router(layers_router)


# ---------------------------------------------------------------------------
# Monitoring / exploitation
# ---------------------------------------------------------------------------
@app.get("/api/health", tags=["monitoring"])
def health():
    """Liveness : l'API repond et le registre SQLite est joignable.
    Utilise par le healthcheck Docker et le load-balancer."""
    try:
        n = len(registry.list_all())
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=503, detail=f"registre injoignable : {exc}") from exc
    return {"status": "ok", "version": __version__, "communes_connues": n}


@app.get("/api/communes", tags=["monitoring"])
def list_communes():
    """Etat de toutes les communes du registre : vue d'exploitation pour reperer
    les generations en echec / en cours et la fraicheur des donnees."""
    communes = [
        {
            "insee": e.insee, "nom": e.nom, "statut": e.statut, "pret": e.est_pret,
            "date_sentinel2": e.date_sentinel2, "date_fwi": e.date_fwi,
            "genere_le": e.genere_le, "maj_le": e.maj_le,
            "erreur": e.erreur.splitlines()[0] if e.erreur else None,
        }
        for e in registry.list_all()
    ]
    return {
        "total": len(communes),
        "en_erreur": sum(1 for c in communes if c["statut"] == "error"),
        "en_cours": sum(1 for c in communes if c["statut"] in ("queued", "running")),
        "communes": communes,
    }


@app.get("/api/refresh/scan", tags=["monitoring"])
def trigger_refresh_scan():
    """Declenche manuellement une passe de rafraichissement (sinon toutes les 12 h).
    Pratique pour tester, ou pour un cron externe."""
    return scheduler.refresh_scan()


# Frontend statique (web/index.html) en dernier : fallback des routes /api/*.
app.mount("/", StaticFiles(directory=str(config.ROOT_DIR / "web"), html=True), name="web")
