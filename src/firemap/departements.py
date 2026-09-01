"""Regions metropolitaines + Corse : departements membres et regions limitrophes.

Sert au calcul du FWI : Meteo-France DPClim n'expose ses stations que par
departement (`id-departement` obligatoire), et un petit departement (Paris,
petite couronne) peut n'avoir AUCUNE station mesurant temperature + humidite +
vent + pluie. On elargit alors la recherche : departement local -> reste de la
region -> regions limitrophes.
"""

REGION_DEPTS: dict[str, list[str]] = {
    "ara": ["01", "03", "07", "15", "26", "38", "42", "43", "63", "69", "73", "74"],
    "bfc": ["21", "25", "39", "58", "70", "71", "89", "90"],
    "bre": ["22", "29", "35", "56"],
    "cvl": ["18", "28", "36", "37", "41", "45"],
    "cor": ["2A", "2B"],
    "ges": ["08", "10", "51", "52", "54", "55", "57", "67", "68", "88"],
    "hdf": ["02", "59", "60", "62", "80"],
    "idf": ["75", "77", "78", "91", "92", "93", "94", "95"],
    "nor": ["14", "27", "50", "61", "76"],
    "naq": ["16", "17", "19", "23", "24", "33", "40", "47", "64", "79", "86", "87"],
    "occ": ["09", "11", "12", "30", "31", "32", "34", "46", "48", "65", "66", "81", "82"],
    "pdl": ["44", "49", "53", "72", "85"],
    "pac": ["04", "05", "06", "13", "83", "84"],
}

REGION_NEIGHBOURS: dict[str, list[str]] = {
    "ara": ["bfc", "cvl", "naq", "occ", "pac"],
    "bfc": ["ara", "ges", "cvl", "idf"],
    "bre": ["pdl", "nor"],
    "cvl": ["idf", "nor", "pdl", "naq", "ara", "bfc"],
    "cor": [],
    "ges": ["bfc", "idf", "hdf"],
    "hdf": ["idf", "ges", "nor"],
    "idf": ["hdf", "ges", "bfc", "cvl", "nor"],
    "nor": ["bre", "pdl", "cvl", "idf", "hdf"],
    "naq": ["cvl", "ara", "occ", "pdl"],
    "occ": ["naq", "ara", "pac"],
    "pdl": ["bre", "nor", "cvl", "naq"],
    "pac": ["ara", "occ"],
}

# departement -> region (inverse de REGION_DEPTS)
DEPT_REGION: dict[str, str] = {d: r for r, deps in REGION_DEPTS.items() for d in deps}


def search_batches(departement: str):
    """Genere des lots de departements a interroger, du plus proche au plus large :
    1) le departement local ; 2) le reste de sa region ; 3) les regions limitrophes.
    On s'arrete des qu'une station exploitable est trouvee (cf. pipeline._step_fwi)."""
    reg = DEPT_REGION.get(departement)
    if reg is None:                      # DOM (971..976) ou code inconnu : local seul
        yield [departement]
        return

    yield [departement]
    yield [d for d in REGION_DEPTS[reg] if d != departement]

    seen = set(REGION_DEPTS[reg])
    autour: list[str] = []
    for r in REGION_NEIGHBOURS.get(reg, []):
        for d in REGION_DEPTS[r]:
            if d not in seen:
                autour.append(d)
                seen.add(d)
    if autour:
        yield autour
