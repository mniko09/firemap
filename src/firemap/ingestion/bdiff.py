"""Historique des feux BDIFF (Base de Donnees sur les Incendies de Foret en
France, bdiff.agriculture.gouv.fr) - fichier CSV telecharge manuellement
depuis le portail (pas d'API publique disponible, cf. note Phase 5).

Limite importante : l'export CSV public de BDIFF geolocalise chaque
incendie a l'echelle de la commune (Code INSEE) uniquement, sans
coordonnees precises ni perimetre. Une verification technique a confirme
que la carte interactive du portail affiche les points via une couche WMS
interne (points_eclosion) sans service WFS/vectoriel public associe -
il n'est donc pas possible d'extraire une geolocalisation precise des
incendies par ce biais.
"""
from pathlib import Path

import pandas as pd

# Le CSV ne code "Type de peuplement" qu'en entier (1-6) sans legende jointe.
# Correspondance deduite de l'ordre enumere dans Definitions.pdf (fourni dans
# l'export BDIFF) : "Landes, garrigues, maquis / taillis / futaies feuillues /
# futaies resineuses / futaies melangees / regeneration et reboisement".
TYPE_PEUPLEMENT_LABELS = {
    1: "Landes, garrigues, maquis",
    2: "Taillis",
    3: "Futaies feuillues",
    4: "Futaies resineuses",
    5: "Futaies melangees",
    6: "Regeneration et reboisement",
}

# Correspondance approximative entre le type de peuplement BDIFF et la
# categorie "nature" IGN BD TOPO utilisee pour ponderer fuel.tif (Phase 3) -
# permet de comparer les types de vegetation touches par les feux historiques
# aux zones que notre carte classe a risque eleve.
TYPE_PEUPLEMENT_TO_IGN_NATURE = {
    "Landes, garrigues, maquis": ["Lande ligneuse"],
    "Taillis": ["Bois", "Forêt fermée de feuillus"],
    "Futaies feuillues": ["Forêt fermée de feuillus"],
    "Futaies resineuses": ["Forêt fermée de conifères"],
    "Futaies melangees": ["Forêt fermée mixte"],
    "Regeneration et reboisement": ["Forêt ouverte"],
}

COLUMN_MAP = [
    "annee", "numero", "departement", "code_insee", "commune", "date_alerte",
    "surface_parcourue_m2", "surface_foret_m2", "surface_maquis_garrigues_m2",
    "surface_autres_naturelles_m2", "surface_agricole_m2", "autres_surfaces_m2",
    "surface_autres_terres_boisees_m2", "surface_non_boisee_naturelle_m2",
    "surface_non_boisee_artificialisee_m2", "surface_non_boisee_m2",
    "precision_surfaces", "type_peuplement", "nature", "deces_ou_batiments_touches",
    "nombre_deces", "nombre_batiments_totalement_detruits",
    "nombre_batiments_partiellement_detruits", "precision_donnee",
]


def load_bdiff_csv(path: Path) -> pd.DataFrame:
    # Le portail BDIFF exporte desormais en UTF-8 (sans BOM) ; d'anciens exports
    # etaient en cp1252. On tente l'UTF-8, repli cp1252.
    try:
        df = pd.read_csv(path, sep=";", skiprows=3, encoding="utf-8")
    except UnicodeDecodeError:
        df = pd.read_csv(path, sep=";", skiprows=3, encoding="cp1252")
    df.columns = COLUMN_MAP
    df["code_insee"] = df["code_insee"].astype(str)
    df["date_alerte"] = pd.to_datetime(df["date_alerte"], format="%Y-%m-%d %H:%M:%S", errors="coerce")
    df["surface_ha"] = df["surface_parcourue_m2"] / 10000
    df["type_peuplement_label"] = df["type_peuplement"].map(
        lambda v: TYPE_PEUPLEMENT_LABELS.get(int(v)) if pd.notna(v) else None
    )
    return df


def filter_commune(df: pd.DataFrame, code_insee: str) -> pd.DataFrame:
    return df[df["code_insee"] == str(code_insee)].sort_values("date_alerte")
