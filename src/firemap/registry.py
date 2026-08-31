"""registry.py -- cache d'etat des communes (SQLite).

Memoire du serveur : pour chaque commune deja demandee, ou en est sa
generation, et sur quelles dates de donnees elle repose. C'est ce qui permet a
l'API de repondre en quelques millisecondes ("servi du cache" / "en cours")
sans jamais calculer dans le fil de la requete HTTP.

Cycle de vie du statut :

    (pas de ligne = "absente")
        |  premiere demande
        v
     queued --> running --> ready --+
        ^           |               |
        |           +--> error      |
        +----------- re-demande ----+
                                    |
     ready --> stale   (Phase 3 : une donnee plus recente est disponible)

Volontairement minimal et derriere une API de fonctions : si on passe un jour
a PostgreSQL/PostGIS, seul ce fichier change.
"""
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal

from . import config

# Un simple fichier a cote des donnees. config.py a deja cree data/ a l'import.
DB_PATH: Path = config.DATA_DIR / "firemap.sqlite"

Statut = Literal["queued", "running", "ready", "error", "stale"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS communes (
    insee           TEXT PRIMARY KEY,
    nom             TEXT,
    statut          TEXT NOT NULL,
    date_sentinel2  TEXT,          -- date (ISO) du composite Sentinel-2 utilise
    date_fwi        TEXT,          -- date (ISO) du dernier jour FWI utilise
    genere_le       TEXT,          -- horodatage ISO de la derniere generation reussie
    erreur          TEXT,          -- message de la derniere erreur (si statut='error')
    maj_le          TEXT NOT NULL  -- horodatage ISO de la derniere mise a jour de la ligne
);
"""


def _now() -> str:
    """Horodatage ISO 8601 UTC (suffixe Z), triable lexicographiquement."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _connect() -> sqlite3.Connection:
    """Connexion courte duree, configuree pour un acces concurrent raisonnable :
    - WAL          : plusieurs lecteurs + 1 ecrivain sans se bloquer ;
    - busy_timeout : on patiente 5 s si la base est momentanement verrouillee
      (plutot que d'echouer aussitot) -- utile avec plusieurs workers.
    """
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """Connexion + transaction : commit si tout va bien, rollback sinon,
    fermeture systematique (on ne garde pas de connexion ouverte entre appels)."""
    conn = _connect()
    try:
        with conn:  # gere commit / rollback
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Cree la table si besoin. Idempotent -- a appeler au demarrage du serveur
    (et au debut d'un job, par securite)."""
    with _db() as conn:
        conn.executescript(_SCHEMA)


@dataclass(frozen=True)
class RegistryEntry:
    insee: str
    nom: str | None
    statut: Statut
    date_sentinel2: str | None
    date_fwi: str | None
    genere_le: str | None
    erreur: str | None
    maj_le: str

    @property
    def est_pret(self) -> bool:
        """True si une carte exploitable existe deja. 'stale' compte : la carte
        est encore servie pendant qu'un rafraichissement tourne en fond."""
        return self.statut in ("ready", "stale")


def _row_to_entry(row: sqlite3.Row) -> RegistryEntry:
    return RegistryEntry(
        insee=row["insee"],
        nom=row["nom"],
        statut=row["statut"],
        date_sentinel2=row["date_sentinel2"],
        date_fwi=row["date_fwi"],
        genere_le=row["genere_le"],
        erreur=row["erreur"],
        maj_le=row["maj_le"],
    )


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------
def get(insee: str) -> RegistryEntry | None:
    """L'entree de cette commune, ou None si elle n'a jamais ete demandee."""
    with _db() as conn:
        row = conn.execute("SELECT * FROM communes WHERE insee = ?", (insee,)).fetchone()
    return _row_to_entry(row) if row else None


def list_all() -> list[RegistryEntry]:
    """Toutes les communes connues, les plus recemment touchees d'abord."""
    with _db() as conn:
        rows = conn.execute("SELECT * FROM communes ORDER BY maj_le DESC").fetchall()
    return [_row_to_entry(r) for r in rows]


# ---------------------------------------------------------------------------
# Transitions d'etat -- chacune est un upsert cible (ne touche que le necessaire)
# ---------------------------------------------------------------------------
def mark_queued(insee: str, nom: str | None = None) -> None:
    """Mise en file. Conserve date_sentinel2 / date_fwi / genere_le existants
    (on sert l'ancienne carte jusqu'a ce que la nouvelle soit prete) et remet
    le message d'erreur a zero."""
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO communes (insee, nom, statut, erreur, maj_le)
            VALUES (:insee, :nom, 'queued', NULL, :now)
            ON CONFLICT(insee) DO UPDATE SET
                statut = 'queued',
                nom    = COALESCE(:nom, communes.nom),
                erreur = NULL,
                maj_le = :now
            """,
            {"insee": insee, "nom": nom, "now": _now()},
        )


def mark_running(insee: str) -> None:
    _set_statut(insee, "running")


def mark_ready(insee: str, *, date_sentinel2: str | None, date_fwi: str | None) -> None:
    """Generation reussie : passe en 'ready', enregistre les dates des donnees
    sources et l'horodatage de generation."""
    with _db() as conn:
        conn.execute(
            """
            UPDATE communes
               SET statut = 'ready',
                   date_sentinel2 = :s2,
                   date_fwi = :fwi,
                   genere_le = :now,
                   erreur = NULL,
                   maj_le = :now
             WHERE insee = :insee
            """,
            {"insee": insee, "s2": date_sentinel2, "fwi": date_fwi, "now": _now()},
        )


def mark_error(insee: str, message: str) -> None:
    with _db() as conn:
        conn.execute(
            "UPDATE communes SET statut='error', erreur=:msg, maj_le=:now WHERE insee=:insee",
            {"insee": insee, "msg": message[:2000], "now": _now()},
        )


def mark_stale(insee: str) -> None:
    """Phase 3 : une passe Sentinel-2 / un releve meteo plus recent est dispo."""
    _set_statut(insee, "stale")


def _set_statut(insee: str, statut: Statut) -> None:
    with _db() as conn:
        conn.execute(
            "UPDATE communes SET statut=:s, maj_le=:now WHERE insee=:insee",
            {"insee": insee, "s": statut, "now": _now()},
        )


# La table est prete des l'import du module (idempotent, quasi instantane).
init_db()
