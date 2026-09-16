"""zone_status.py -- suivi "deja traite" des zones prioritaires retardant.

Checklist partagee (SQLite, meme base que registry.py) entre tous les
utilisateurs de la plateforme pour une commune donnee : coche par l'un,
visible par les autres. Cle par zone_key (cf. risk/priorisation.py), STABLE
d'un rafraichissement a l'autre -- pas par "id" (qui, lui, depend du tri par
score et peut changer de zone a chaque regeneration).
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from . import config

DB_PATH = config.DATA_DIR / "firemap.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS zones_traitees (
    insee      TEXT NOT NULL,
    zone_key   TEXT NOT NULL,
    traite_le  TEXT NOT NULL,
    PRIMARY KEY (insee, zone_key)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    with _db() as conn:
        conn.executescript(_SCHEMA)


def get_traitees(insee: str) -> dict[str, str]:
    """{zone_key: traite_le} pour les zones cochees "traite" de cette commune.
    Une zone absente du dict = pas traitee (pas de ligne "false" a gerer)."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT zone_key, traite_le FROM zones_traitees WHERE insee = ?", (insee,)
        ).fetchall()
    return {r["zone_key"]: r["traite_le"] for r in rows}


def set_traitee(insee: str, zone_key: str, traite: bool) -> None:
    with _db() as conn:
        if traite:
            conn.execute(
                """
                INSERT INTO zones_traitees (insee, zone_key, traite_le)
                VALUES (:insee, :zone_key, :now)
                ON CONFLICT(insee, zone_key) DO UPDATE SET traite_le = :now
                """,
                {"insee": insee, "zone_key": zone_key, "now": _now()},
            )
        else:
            conn.execute(
                "DELETE FROM zones_traitees WHERE insee = ? AND zone_key = ?",
                (insee, zone_key),
            )


init_db()
