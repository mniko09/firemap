"""Registre SQLite : cycle de vie et conservation des dates lors d'une re-demande."""
import pytest

from firemap import registry


@pytest.fixture
def reg(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "DB_PATH", tmp_path / "t.sqlite")
    registry.init_db()
    return registry


def test_commune_absente(reg):
    assert reg.get("13055") is None


def test_cycle_queued_running_ready(reg):
    reg.mark_queued("83130", nom="Solliès-Pont")
    assert reg.get("83130").statut == "queued"

    reg.mark_running("83130")
    assert reg.get("83130").statut == "running"

    reg.mark_ready("83130", date_sentinel2="2026-08-31", date_fwi="2026-08-30")
    e = reg.get("83130")
    assert e.statut == "ready" and e.est_pret
    assert (e.date_sentinel2, e.date_fwi) == ("2026-08-31", "2026-08-30")
    assert e.genere_le is not None


def test_re_demande_conserve_les_dates(reg):
    reg.mark_queued("83130")
    reg.mark_ready("83130", date_sentinel2="2026-08-31", date_fwi="2026-08-30")
    genere_le = reg.get("83130").genere_le

    reg.mark_queued("83130")            # rafraichissement
    e = reg.get("83130")
    assert e.statut == "queued"
    assert (e.date_sentinel2, e.date_fwi, e.genere_le) == ("2026-08-31", "2026-08-30", genere_le)


def test_erreur_et_stale(reg):
    reg.mark_queued("13001")
    reg.mark_error("13001", "CDSE 500")
    e = reg.get("13001")
    assert e.statut == "error" and e.erreur == "CDSE 500" and not e.est_pret

    reg.mark_ready("13001", date_sentinel2="2026-08-25", date_fwi="2026-08-24")
    reg.mark_stale("13001")
    assert reg.get("13001").statut == "stale"
    assert reg.get("13001").est_pret          # stale = carte encore servable
