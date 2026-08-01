"""Enjeux a proteger : ICPE/SEVESO (Georisques, API ouverte) + etablissements
scolaires (annuaire education.gouv.fr, API ouverte). Aucune cle API necessaire.
Le bati sensible (EHPAD) et les installations nucleaires (ASN) ne sont pas
encore integres - cf. limites signalees en Phase 3.
"""
import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import Point

GEORISQUES_URL = "https://georisques.gouv.fr/api/v1/installations_classees"
EDUCATION_URL = "https://data.education.gouv.fr/api/records/1.0/search/"


def fetch_icpe(code_insee: str) -> gpd.GeoDataFrame:
    """Installations classees (ICPE/SEVESO) sur la commune, via l'API ouverte Georisques."""
    records = []
    page = 1
    while True:
        resp = requests.get(
            GEORISQUES_URL,
            params={"code_insee": code_insee, "page": page, "page_size": 50},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        records.extend(payload["data"])
        if page >= payload.get("total_pages", 1):
            break
        page += 1

    if not records:
        return gpd.GeoDataFrame(columns=["raisonSociale", "statutSeveso", "geometry"], crs="EPSG:4326")

    df = pd.DataFrame(records)
    geometry = [Point(lon, lat) for lon, lat in zip(df["longitude"], df["latitude"])]
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")
    gdf["categorie"] = "ICPE"
    return gdf[["raisonSociale", "statutSeveso", "categorie", "geometry"]]


def fetch_ecoles(code_insee: str) -> gpd.GeoDataFrame:
    """Etablissements scolaires sur la commune, via l'annuaire ouvert education.gouv.fr."""
    resp = requests.get(
        EDUCATION_URL,
        params={
            "dataset": "fr-en-annuaire-education",
            "refine.code_commune": code_insee,
            "rows": 100,
        },
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()

    records = []
    for rec in payload.get("records", []):
        f = rec["fields"]
        if f.get("etat") != "OUVERT":
            continue
        records.append(
            {
                "nom_etablissement": f.get("nom_etablissement"),
                "type_etablissement": f.get("type_etablissement"),
                "categorie": "Etablissement scolaire",
                "geometry": Point(f["longitude"], f["latitude"]),
            }
        )

    if not records:
        return gpd.GeoDataFrame(columns=["nom_etablissement", "categorie", "geometry"], crs="EPSG:4326")

    return gpd.GeoDataFrame(records, crs="EPSG:4326")


def fetch_enjeux_points(code_insee: str) -> gpd.GeoDataFrame:
    """Fusionne ICPE/SEVESO et etablissements scolaires en une seule couche de points."""
    icpe = fetch_icpe(code_insee)
    ecoles = fetch_ecoles(code_insee)
    combined = pd.concat([icpe[["categorie", "geometry"]], ecoles[["categorie", "geometry"]]], ignore_index=True)
    return gpd.GeoDataFrame(combined, crs="EPSG:4326")
