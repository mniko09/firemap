"""Routes de gestion des communes (v2) : recherche, statut, declenchement.

Ces routes ne calculent JAMAIS dans le fil de la requete : elles lisent le
registre et, si besoin, delegent a firemap.jobs (tache de fond). Le frontend
appelle /search pour resoudre un nom, puis /status en boucle courte.
"""
import requests
from fastapi import APIRouter, HTTPException, Query, Response, status

from .. import jobs, registry
from ..http import SESSION

router = APIRouter(prefix="/api/communes", tags=["communes"])

_GEO_API = "https://geo.api.gouv.fr/communes"


def _valid_insee(insee: str) -> bool:
    """5 caracteres : soit 5 chiffres, soit Corse (2A/2B + 3 chiffres)."""
    return len(insee) == 5 and (
        insee.isdigit()
        or (insee[:2].upper() in ("2A", "2B") and insee[2:].isdigit())
    )


# ---------------------------------------------------------------------------
# Recherche / autocompletion : nom -> code INSEE (objectif 3.2 du cahier)
# ---------------------------------------------------------------------------
@router.get("/search")
def search_communes(q: str = Query(..., min_length=1, description="debut de nom de commune")):
    """Autocompletion via geo.api.gouv.fr (API ouverte, sans cle).
    Renvoie au plus 10 communes, les plus peuplees d'abord :
    [{insee, nom, code_departement, population}]."""
    try:
        resp = SESSION.get(
            _GEO_API,
            params={
                "nom": q,
                "fields": "nom,code,codeDepartement,population",
                "boost": "population",
                "limit": 10,
            },
            timeout=(5, 15),   # autocompletion : on ne veut pas faire trop attendre
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"geo.api.gouv.fr indisponible : {exc}") from exc

    return [
        {
            "insee": c["code"],
            "nom": c["nom"],
            "code_departement": c.get("codeDepartement"),
            "population": c.get("population"),
        }
        for c in resp.json()
    ]


# ---------------------------------------------------------------------------
# Statut / fraicheur (objectif 3.3 : afficher la date de la donnee)
# ---------------------------------------------------------------------------
def _status_payload(insee: str) -> dict:
    e = registry.get(insee)
    if e is None:
        return {"insee": insee, "statut": "absent", "pret": False}
    return {
        "insee": e.insee,
        "nom": e.nom,
        "statut": e.statut,          # queued | running | ready | stale | error
        "pret": e.est_pret,          # True si une carte est deja servable
        "date_sentinel2": e.date_sentinel2,
        "date_fwi": e.date_fwi,
        "genere_le": e.genere_le,
        "erreur": e.erreur,
    }


@router.get("/{insee}/status")
def commune_status(insee: str, response: Response):
    """Etat de la commune.
      - absent / error / ready / stale -> 200 (le frontend decide s'il appelle /generate)
      - queued / running               -> 202 (generation en cours)
    """
    if not _valid_insee(insee):
        raise HTTPException(status_code=422, detail="code INSEE invalide")
    payload = _status_payload(insee)
    if payload["statut"] in ("queued", "running"):
        response.status_code = status.HTTP_202_ACCEPTED
    return payload


# ---------------------------------------------------------------------------
# Declenchement d'une generation (a la demande + cache, cf. §3.6)
# ---------------------------------------------------------------------------
@router.post("/{insee}/generate", status_code=status.HTTP_202_ACCEPTED)
def generate_commune(
    insee: str,
    nom: str | None = Query(None, description="nom affiche (optionnel)"),
    force: bool = Query(False, description="regenerer meme si deja en cache"),
):
    """Met une generation en file. Idempotent : si elle est deja en cours, on ne
    relance pas. Repond immediatement avec le statut courant (jamais bloquant)."""
    if not _valid_insee(insee):
        raise HTTPException(status_code=422, detail="code INSEE invalide")
    jobs.submit(insee, nom, force=force)
    return _status_payload(insee)
