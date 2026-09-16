"""Routes de suivi des zones prioritaires (checklist "deja traite") :
  GET  /api/communes/{insee}/zones/statut              {zone_key: traite_le}
  POST /api/communes/{insee}/zones/{zone_key}/statut   {"traite": bool}
"""
from fastapi import APIRouter
from pydantic import BaseModel

from .. import zone_status

router = APIRouter(prefix="/api/communes/{insee}/zones", tags=["zones"])


class StatutIn(BaseModel):
    traite: bool


@router.get("/statut")
def get_statut(insee: str):
    return zone_status.get_traitees(insee)


@router.post("/{zone_key}/statut")
def set_statut(insee: str, zone_key: str, body: StatutIn):
    zone_status.set_traitee(insee, zone_key, body.traite)
    return {"zone_key": zone_key, "traite": body.traite}
