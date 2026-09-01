"""CommuneContext : chemins isoles, departement, egalite par INSEE."""
from firemap import config
from firemap.context import CommuneContext


def test_paths_isoles_par_commune():
    ctx = CommuneContext("83130", nom="Solliès-Pont")
    assert ctx.root == config.COMMUNES_DIR / "83130"
    assert ctx.processed("ndvi.tif") == config.COMMUNES_DIR / "83130" / "processed" / "ndvi.tif"
    assert ctx.boundary("commune_l93.geojson").parent.name == "boundaries"
    assert ctx.metadata_path.name == "metadata.json"


def test_departement_derive_du_code():
    assert CommuneContext("83130").departement == "83"
    assert CommuneContext("13055").departement == "13"
    assert CommuneContext("2A004").departement == "2A"   # Corse
    assert CommuneContext("97101").departement == "97"   # DOM


def test_egalite_par_insee_seulement():
    a = CommuneContext("83130", nom="Solliès-Pont")
    b = CommuneContext("83130", nom="SOLLIES PONT")
    assert a == b and hash(a) == hash(b)
    assert {a: 1}[b] == 1
    assert CommuneContext("83086") != a
