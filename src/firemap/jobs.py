"""jobs.py -- execution des generations en tache de fond (in-process).

Choix Phase 1 : un simple ThreadPoolExecutor (2 generations max en parallele),
aucune infra externe. Le pipeline est surtout IO-bound (telechargements S2 /
WMS / WFS / DPClim) et les briques lourdes (rasterio, numpy) relachent le GIL,
donc des threads suffisent a decoupler l'API du calcul. La limite de 2 protege
aussi le quota Meteo-France DPClim (50 req/min).

Pour passer a des processus, ou a RQ/Celery plus tard : seul ce fichier change
(on garde la signature de submit() et la mise a jour du registre).
"""
import threading
from concurrent.futures import ThreadPoolExecutor

from . import registry
from .pipeline import run as _pipeline_run

_MAX_PARALLEL = 2

_executor = ThreadPoolExecutor(max_workers=_MAX_PARALLEL, thread_name_prefix="firemap-gen")
_inflight: set[str] = set()          # communes en cours DANS CE PROCESSUS
_lock = threading.Lock()


def _run_job(insee: str, nom: str | None, force: bool) -> None:
    """Corps du thread : lance le pipeline, nettoie l'etat 'in-flight' a la fin."""
    try:
        _pipeline_run(insee, nom=nom, force=force)
    except Exception:
        # pipeline.run a deja enregistre l'erreur (registry.mark_error) et
        # re-leve. On avale ici pour ne pas casser le pool ; le statut 'error'
        # est visible via /status.
        pass
    finally:
        with _lock:
            _inflight.discard(insee)


def is_running(insee: str) -> bool:
    """True si une generation de cette commune tourne dans ce processus."""
    with _lock:
        return insee in _inflight


def submit(insee: str, nom: str | None = None, *, force: bool = False) -> "registry.RegistryEntry | None":
    """Met une generation en file (si pas deja en cours ici) et renvoie l'entree
    de registre courante. Ne bloque JAMAIS."""
    with _lock:
        already = insee in _inflight
        if not already:
            _inflight.add(insee)

    if not already:
        registry.mark_queued(insee, nom)
        _executor.submit(_run_job, insee, nom, force)

    return registry.get(insee)


def reset_orphans() -> int:
    """A appeler au demarrage du serveur : toute commune restee 'queued' ou
    'running' vient forcement d'un processus tue (les jobs in-process ne
    survivent pas a un redemarrage). On les repasse en 'error' pour ne pas les
    laisser bloquees. Retourne le nombre de lignes corrigees."""
    n = 0
    for e in registry.list_all():
        if e.statut in ("queued", "running"):
            registry.mark_error(e.insee, "Interrompu par un redemarrage du serveur.")
            n += 1
    return n
