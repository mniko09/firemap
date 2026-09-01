"""scheduler.py -- rafraichissement automatique (cahier §4.5).

Un BackgroundScheduler APScheduler (in-process, demarre/arrete avec l'API) qui,
toutes les _INTERVAL_HOURS, passe en revue les communes en cache : si une source
(Sentinel-2 / Meteo-France) a une donnee plus recente, on invalide UNIQUEMENT
les couches concernees et on renfile une generation (l'idempotence du pipeline
recalcule juste ce qui manque).

Au demarrage, on s'assure aussi que les communes "prioritaires"
(data/priority_communes.json, a definir avec SELVERT -- cf. §6) sont generees :
c'est le socle "toujours pret" pour les demonstrations commerciales.
"""
import json

from apscheduler.schedulers.background import BackgroundScheduler

from . import config, freshness, jobs, registry
from .context import CommuneContext

_INTERVAL_HOURS = 12
_PRIORITY_FILE = config.DATA_DIR / "priority_communes.json"

# Raster source a supprimer selon la source perimee. On ne touche QUE ces
# fichiers : le pipeline, rejoue, regenere l'aval (risk, priorites, COG) et
# ECRASE en place les fichiers servis une fois les nouvelles valeurs pretes
# (gardes d'idempotence basees sur les dates de modification -> _outdated).
# MNT / vegetation / enjeux ne sont pas surveilles (evoluent a l'echelle de l'annee).
_BY_SOURCE = {"sentinel2": ["ndvi.tif", "ndmi.tif"], "fwi": ["fwi.tif"]}

_scheduler: BackgroundScheduler | None = None


# ---------------------------------------------------------------------------
def load_priority_communes() -> list[str]:
    try:
        return [str(x) for x in json.loads(_PRIORITY_FILE.read_text(encoding="utf-8"))]
    except (OSError, ValueError):
        return []


def _invalidate(ctx: CommuneContext, sources: list[str]) -> int:
    """Supprime UNIQUEMENT les rasters sources a recalculer (fwi.tif, ou
    ndvi.tif + ndmi.tif). Rien d'autre : les fichiers servis (COG, priorites,
    metadata) restent en place et valides ; le pipeline les ecrasera une fois
    les nouvelles valeurs pretes."""
    deleted = 0
    for src in sources:
        for name in _BY_SOURCE.get(src, []):
            p = ctx.processed(name)
            if p.exists():
                p.unlink()
                deleted += 1
    return deleted


# ---------------------------------------------------------------------------
def refresh_scan() -> dict:
    """Coeur du planificateur : regenere les communes en cache dont une source
    est perimee. Aussi expose en GET /api/refresh/scan (declenchement manuel)."""
    report = {"scanned": 0, "refreshed": [], "up_to_date": [], "skipped": []}

    for e in registry.list_all():
        if e.statut not in ("ready", "stale"):
            report["skipped"].append(e.insee)          # queued/running/error : on laisse
            continue
        report["scanned"] += 1

        ctx = CommuneContext(e.insee, nom=e.nom)
        stale_sources = freshness.commune_is_stale(
            ctx, sentinel2_asof=e.date_sentinel2, date_fwi=e.date_fwi
        )
        if not stale_sources:
            report["up_to_date"].append(e.insee)
            continue

        deleted = _invalidate(ctx, stale_sources)
        registry.mark_stale(e.insee)
        jobs.submit(e.insee, e.nom)                     # tache de fond (worker Phase 1)
        report["refreshed"].append(
            {"insee": e.insee, "sources": stale_sources, "fichiers_supprimes": deleted}
        )
        print(f"[refresh] {e.insee} perime ({', '.join(stale_sources)}) -> regeneration", flush=True)

    return report


def ensure_priority_communes() -> list[str]:
    """Genere les communes prioritaires absentes ou en echec (socle 'toujours pret')."""
    lances = []
    for insee in load_priority_communes():
        e = registry.get(insee)
        if e is None or e.statut == "error":
            jobs.submit(insee)
            lances.append(insee)
    if lances:
        print(f"[startup] communes prioritaires (re)lancees : {', '.join(lances)}", flush=True)
    return lances


# ---------------------------------------------------------------------------
def start_scheduler() -> None:
    """Demarre le planificateur (idempotent). A appeler au demarrage de l'API."""
    global _scheduler
    if _scheduler is not None:
        return
    ensure_priority_communes()

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        refresh_scan, "interval", hours=_INTERVAL_HOURS,
        id="refresh_scan", max_instances=1, coalesce=True,
        next_run_time=None,   # pas de scan au demarrage (evite une rafale a chaque redemarrage)
    )
    _scheduler.start()
    print(f"[scheduler] rafraichissement auto toutes les {_INTERVAL_HOURS} h", flush=True)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
