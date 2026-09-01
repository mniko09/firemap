"""Expansion de la recherche de station meteo (region -> regions limitrophes)."""
from firemap.departements import DEPT_REGION, REGION_DEPTS, search_batches


def test_couverture_metropole_plus_corse():
    assert len(DEPT_REGION) == 96          # 94 metropole + 2A + 2B
    assert DEPT_REGION["83"] == "pac"
    assert DEPT_REGION["75"] == "idf"
    assert DEPT_REGION["2A"] == "cor"


def test_batches_paris():
    lots = list(search_batches("75"))
    assert lots[0] == ["75"]                        # departement local d'abord
    assert set(lots[1]) == set(REGION_DEPTS["idf"]) - {"75"}   # reste de l'Ile-de-France
    assert "78" in lots[1] and "91" in lots[1]      # Villacoublay / Orly : stations completes
    assert lots[2] and "75" not in lots[2]          # regions limitrophes, sans doublon


def test_batches_var_fast_path():
    lots = list(search_batches("83"))
    assert lots[0] == ["83"]
    assert set(lots[1]) == {"04", "05", "06", "13", "84"}   # reste de PACA


def test_hors_metropole_local_seul():
    assert list(search_batches("971")) == [["971"]]
