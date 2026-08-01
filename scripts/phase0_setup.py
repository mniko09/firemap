"""
Phase 0 - Cadrage & environnement
Verifie les deux criteres de validation de la Phase 0 :
1. Le contour de la commune pilote (Le Muy, 83) s'affiche.
2. Les identifiants CDSE (Copernicus Data Space Ecosystem) fonctionnent.
"""
import os

import matplotlib.pyplot as plt
import requests
from dotenv import load_dotenv

from firemap import config
from firemap.ingestion.commune import load_or_fetch_commune


def main_contour():
    gdf, gdf_l93 = load_or_fetch_commune()
    row = gdf.iloc[0]
    print(f"Commune trouvee : {row['nom']} (INSEE {row['code']}), "
          f"surface ~{row['surface'] / 100:.1f} km2")
    print(f"Contour sauvegarde -> {config.BOUNDARIES_DIR / 'commune.geojson'}")
    print(f"Contour (EPSG:2154) sauvegarde -> {config.BOUNDARIES_DIR / 'commune_l93.geojson'}")

    fig, ax = plt.subplots(figsize=(6, 6))
    gdf.boundary.plot(ax=ax, color="firebrick", linewidth=2)
    gdf.plot(ax=ax, color="orange", alpha=0.15)
    ax.set_title(f"Contour communal - {row['nom']} (INSEE {row['code']})")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    fig.tight_layout()
    png_path = config.OUTPUTS_DIR / "commune_contour.png"
    fig.savefig(png_path, dpi=150)
    print(f"Carte sauvegardee -> {png_path}")

    return gdf


# ---------------------------------------------------------------------------
# Test des identifiants CDSE (OAuth client_credentials)
# ---------------------------------------------------------------------------
def check_cdse_credentials() -> bool:
    load_dotenv(config.ROOT_DIR / ".env")
    client_id = os.getenv("CDSE_CLIENT_ID")
    client_secret = os.getenv("CDSE_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("CDSE : identifiants absents du .env")
        return False

    token_url = (
        "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
        "protocol/openid-connect/token"
    )
    resp = requests.post(
        token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=30,
    )

    if resp.status_code == 200 and "access_token" in resp.json():
        expires_in = resp.json().get("expires_in")
        print(f"CDSE OK : token obtenu (validite {expires_in}s). "
              f"Identifiants fonctionnels.")
        return True

    print(f"CDSE ECHEC : HTTP {resp.status_code} - {resp.text[:300]}")
    return False


if __name__ == "__main__":
    print("=== Phase 0 : verification des criteres de validation ===\n")
    print("--- 1) Contour de la commune ---")
    main_contour()
    print("\n--- 2) Identifiants CDSE ---")
    cdse_ok = check_cdse_credentials()

    print("\n=== Resultat ===")
    print("Contour commune : OK")
    print(f"Identifiants CDSE : {'OK' if cdse_ok else 'ECHEC'}")
