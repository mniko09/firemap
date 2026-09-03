"""[5] FWI / danger meteo - donnees quotidiennes Meteo-France (API DPClim,
portail-api.meteofrance.fr, necessite une cle API gratuite) + calcul du FWI
selon le systeme canadien Foret-Meteo (Van Wagner & Pickett, 1985).

Approximation assumee : les codes du systeme canadien sont normalement calcules
a partir d'observations de 12h locales. Faute de releves horaires, on utilise
les agregats quotidiens les plus proches des conditions d'apres-midi (pic de
danger) : TX (temperature max), UN (humidite min), FFM (vent moyen), RR (pluie
24h). C'est une simplification standard pour un calcul a partir d'archives
quotidiennes.
"""
import os
import time
from io import StringIO

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from .. import config
from ..http import SESSION

DPCLIM_BASE = "https://public-api.meteofrance.fr/public/DPClim/v1"

# Facteurs de longueur du jour (hemisphere Nord, Van Wagner & Pickett 1985)
LE_BY_MONTH = [6.5, 7.5, 9.0, 12.8, 13.9, 13.9, 12.4, 10.9, 9.4, 8.0, 7.0, 6.0]
LF_BY_MONTH = [-1.6, -1.6, -1.6, 0.9, 3.8, 5.8, 6.4, 5.0, 2.4, 0.4, -1.6, -1.6]

# Codes de demarrage standard (debut de saison), utilises comme point de
# depart de la periode de "spin-up" (~90 jours ici, largement suffisant).
FFMC0, DMC0, DC0 = 85.0, 6.0, 15.0


def _api_key() -> str:
    load_dotenv(config.ROOT_DIR / ".env")
    return os.environ["METEOFRANCE_API_KEY"]


def _open_stations(departement: str) -> list[dict]:
    """Stations 'quotidienne' OUVERTES d'un departement (DPClim exige id-departement)."""
    resp = SESSION.get(
        f"{DPCLIM_BASE}/liste-stations/quotidienne",
        headers={"apikey": _api_key()},
        params={"id-departement": departement},
    )
    resp.raise_for_status()
    return [s for s in resp.json() if s.get("posteOuvert")]


def nearest_open_stations(lat: float, lon: float, departements: list[str]) -> list[dict]:
    """Stations ouvertes des departements donnes, triees par distance au point.
    (Toutes ne mesurent pas l'humidite / le vent -> l'appelant essaie les
    premieres jusqu'a en trouver une qui donne un FWI exploitable.)"""
    pool: list[dict] = []
    for dep in departements:
        pool.extend(_open_stations(dep))
    pool.sort(key=lambda s: (s["lat"] - lat) ** 2 + (s["lon"] - lon) ** 2)
    return pool


def find_nearest_station(lat: float, lon: float, departement: str) -> dict:
    """Station la plus proche d'un seul departement. Conserve pour le script
    legacy scripts/phase3b_fwi.py ; le pipeline v2 passe par nearest_open_stations."""
    return nearest_open_stations(lat, lon, [departement])[0]


def fetch_daily_weather(station_id: str, date_deb: str, date_fin: str) -> pd.DataFrame:
    """Commande + telechargement des donnees climatologiques quotidiennes (CSV) DPClim."""
    headers = {"apikey": _api_key()}
    resp = SESSION.get(
        f"{DPCLIM_BASE}/commande-station/quotidienne",
        headers=headers,
        params={
            "id-station": station_id,
            "date-deb-periode": date_deb,
            "date-fin-periode": date_fin,
        },
    )
    resp.raise_for_status()
    cmde_id = resp.json()["elaboreProduitAvecDemandeResponse"]["return"]

    # Le fichier est prepare de facon ASYNCHRONE cote Meteo-France. Tant qu'il
    # n'est pas pret, DPClim repond 204 OU 404 (selon la charge / le moment) --
    # ce n'est pas une vraie erreur, on continue d'interroger. 201 = pret.
    for _ in range(30):
        resp = SESSION.get(
            f"{DPCLIM_BASE}/commande/fichier",
            headers=headers,
            params={"id-cmde": cmde_id},
        )
        if resp.status_code == 201:
            break
        if resp.status_code in (204, 404, 500, 502, 503):
            time.sleep(3)
            continue
        resp.raise_for_status()
    else:
        raise TimeoutError(
            f"DPClim : fichier (commande {cmde_id}) non pret apres ~90 s -- reessayer plus tard"
        )

    df = pd.read_csv(StringIO(resp.text), sep=";", decimal=",")
    df["DATE"] = pd.to_datetime(df["DATE"], format="%Y%m%d")
    return df.sort_values("DATE").reset_index(drop=True)


def _ffmc(ffmc_prev, temp, rh, wind_kmh, rain):
    mo = 147.2 * (101 - ffmc_prev) / (59.5 + ffmc_prev)
    if rain > 0.5:
        rf = rain - 0.5
        if mo <= 150:
            mr = mo + 42.5 * rf * np.exp(-100 / (251 - mo)) * (1 - np.exp(-6.93 / rf))
        else:
            mr = (
                mo + 42.5 * rf * np.exp(-100 / (251 - mo)) * (1 - np.exp(-6.93 / rf))
                + 0.0015 * (mo - 150) ** 2 * np.sqrt(rf)
            )
        mo = min(mr, 250)

    ed = (
        0.942 * rh**0.679 + 11 * np.exp((rh - 100) / 10)
        + 0.18 * (21.1 - temp) * (1 - np.exp(-0.115 * rh))
    )
    if mo > ed:
        ko = 0.424 * (1 - (rh / 100) ** 1.7) + 0.0694 * np.sqrt(wind_kmh) * (1 - (rh / 100) ** 8)
        kd = ko * 0.581 * np.exp(0.0365 * temp)
        m = ed + (mo - ed) * 10 ** (-kd)
    else:
        ew = (
            0.618 * rh**0.753 + 10 * np.exp((rh - 100) / 10)
            + 0.18 * (21.1 - temp) * (1 - np.exp(-0.115 * rh))
        )
        if mo < ew:
            k1 = 0.424 * (1 - ((100 - rh) / 100) ** 1.7) + 0.0694 * np.sqrt(wind_kmh) * (1 - ((100 - rh) / 100) ** 8)
            kw = k1 * 0.581 * np.exp(0.0365 * temp)
            m = ew - (ew - mo) * 10 ** (-kw)
        else:
            m = mo

    return 59.5 * (250 - m) / (147.2 + m)


def _dmc(dmc_prev, temp, rh, rain, month):
    le = LE_BY_MONTH[month - 1]
    if rain > 1.5:
        re = 0.92 * rain - 1.27
        mo = 20 + np.exp(5.6348 - dmc_prev / 43.43)
        if dmc_prev <= 33:
            b = 100 / (0.5 + 0.3 * dmc_prev)
        elif dmc_prev <= 65:
            b = 14 - 1.3 * np.log(dmc_prev)
        else:
            b = 6.2 * np.log(dmc_prev) - 17.2
        mr = mo + 1000 * re / (48.77 + b * re)
        pr = max(244.72 - 43.43 * np.log(mr - 20), 0)
    else:
        pr = dmc_prev

    k = 1.894 * (temp + 1.1) * (100 - rh) * le * 1e-4 if temp > -1.1 else 0.0
    return pr + k


def _dc(dc_prev, temp, rain, month):
    lf = LF_BY_MONTH[month - 1]
    if rain > 2.8:
        rd = 0.83 * rain - 1.27
        qo = 800 * np.exp(-dc_prev / 400)
        qr = qo + 3.937 * rd
        dr = max(400 * np.log(800 / qr), 0)
    else:
        dr = dc_prev

    v = max(0.36 * (temp + 2.8) + lf, 0)
    return dr + 0.5 * v


def _isi(ffmc, wind_kmh):
    m = 147.2 * (101 - ffmc) / (59.5 + ffmc)
    ff = 91.9 * np.exp(-0.1386 * m) * (1 + m**5.31 / 4.93e7)
    fw = np.exp(0.05039 * wind_kmh)
    return 0.208 * fw * ff


def _bui(dmc, dc):
    denom = dmc + 0.4 * dc
    if denom <= 0:
        return 0.0
    if dmc <= 0.4 * dc:
        return 0.8 * dmc * dc / denom
    return dmc - (1 - 0.8 * dc / denom) * (0.92 + (0.0114 * dmc) ** 1.7)


def _fwi(isi, bui):
    fd = 0.626 * bui**0.809 + 2.0 if bui <= 80 else 1000 / (25 + 108.64 * np.exp(-0.023 * bui))
    b = 0.1 * isi * fd
    return np.exp(2.72 * (0.434 * np.log(b)) ** 0.647) if b > 1 else b


def compute_fwi_series(weather: pd.DataFrame) -> pd.DataFrame:
    """Calcule FFMC/DMC/DC/ISI/BUI/FWI jour par jour a partir des colonnes
    DATE, TX (temp max C), UN (humidite min %), FFM (vent moyen m/s), RR (pluie 24h mm)."""
    records = []
    ffmc, dmc, dc = FFMC0, DMC0, DC0

    for _, row in weather.iterrows():
        temp, rh, wind_ms, rain = row["TX"], row["UN"], row["FFM"], row["RR"]

        if pd.isna(temp) or pd.isna(rh) or pd.isna(wind_ms):
            records.append({"DATE": row["DATE"], "FFMC": np.nan, "DMC": np.nan,
                             "DC": np.nan, "ISI": np.nan, "BUI": np.nan, "FWI": np.nan})
            continue

        rh = min(max(rh, 0.0), 100.0)
        rain = 0.0 if pd.isna(rain) else rain
        wind_kmh = wind_ms * 3.6
        month = row["DATE"].month

        ffmc = _ffmc(ffmc, temp, rh, wind_kmh, rain)
        dmc = max(_dmc(dmc, temp, rh, rain, month), 0.0)
        dc = max(_dc(dc, temp, rain, month), 0.0)
        isi = _isi(ffmc, wind_kmh)
        bui = _bui(dmc, dc)
        fwi = _fwi(isi, bui)

        records.append({"DATE": row["DATE"], "FFMC": ffmc, "DMC": dmc, "DC": dc,
                         "ISI": isi, "BUI": bui, "FWI": fwi})

    return pd.DataFrame(records)
