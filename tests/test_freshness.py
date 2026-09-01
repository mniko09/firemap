"""Detection de fraicheur -- volet sans reseau (arithmetique de dates)."""
import datetime as dt

from firemap import freshness
from firemap.context import CommuneContext

TODAY = dt.date(2026, 8, 31)


def test_fwi_perime_au_dela_de_2_jours():
    assert freshness.fwi_has_newer("2026-08-30", today=TODAY) is False   # 1 j
    assert freshness.fwi_has_newer("2026-08-29", today=TODAY) is False   # 2 j
    assert freshness.fwi_has_newer("2026-08-28", today=TODAY) is True    # 3 j
    assert freshness.fwi_has_newer(None, today=TODAY) is True            # inconnu


def test_sentinel2_court_circuit_si_deja_aujourdhui():
    # sentinel2_asof == today -> pas d'appel catalogue, retour False immediat
    ctx = CommuneContext("83130")
    assert freshness.sentinel2_has_newer(ctx, "2026-08-31", today=TODAY) is False


def test_commune_is_stale_liste_les_sources():
    ctx = CommuneContext("83130")
    # S2 asof == today -> court-circuit (pas de reseau) ; FWI hier -> a jour
    assert freshness.commune_is_stale(
        ctx, sentinel2_asof="2026-08-31", date_fwi="2026-08-30", today=TODAY) == []
    # FWI vieux -> au moins 'fwi'
    assert "fwi" in freshness.commune_is_stale(
        ctx, sentinel2_asof="2026-08-31", date_fwi="2026-07-01", today=TODAY)
