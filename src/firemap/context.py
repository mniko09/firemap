"""CommuneContext : tout ce qui, dans le pipeline, depend de LA commune traitee.

Le v1 portait la commune dans des variables globales de config.py
(COMMUNE_CODE_INSEE) et ecrivait dans un unique data/processed/. Impossible de
servir plusieurs communes sans qu'elles s'ecrasent, et impossible de tester
proprement (tout depend d'un etat global implicite).

On remplace ces globales par un objet EXPLICITE, cree par requete / par job,
qui :
  - porte le code INSEE (et le nom, optionnel, juste pour l'affichage) ;
  - expose des chemins ISOLES sous data/communes/<INSEE>/ ;
  - derive le departement (utile pour cibler les stations Meteo-France).

Aucune logique metier ici : uniquement de l'etat et des chemins.
"""
from dataclasses import dataclass, field
from pathlib import Path

from . import config


@dataclass(frozen=True)
class CommuneContext:
    """Identifie une commune et localise tous ses fichiers derives.

    `frozen=True` : l'objet est immuable (donc hashable, sans risque d'etre
    modifie par effet de bord une fois passe a une fonction).
    Le `nom` n'entre pas dans l'egalite/hash (`compare=False`) : deux contextes
    sont "la meme commune" s'ils ont le meme INSEE, quel que soit le libelle.
    """

    insee: str
    nom: str | None = field(default=None, compare=False)

    # ------------------------------------------------------------------
    # Departement
    # ------------------------------------------------------------------
    @property
    def departement(self) -> str:
        """2 premiers caracteres du code INSEE.
        Corse : "2A" / "2B" ; metropole & DOM : 2 chiffres ("83", "13", "971"...).
        Utilise par l'ingestion FWI pour ne chercher que les stations du bon dept.
        """
        return self.insee[:2].upper()

    # ------------------------------------------------------------------
    # Arborescence isolee : data/communes/<INSEE>/...
    # ------------------------------------------------------------------
    @property
    def root(self) -> Path:
        return config.COMMUNES_DIR / self.insee

    @property
    def boundaries_dir(self) -> Path:
        """Contours de la commune (GeoJSON WGS84 + Lambert-93)."""
        return self.root / "boundaries"

    @property
    def raw_dir(self) -> Path:
        """Telechargements bruts non retravailles (ex. export BDIFF)."""
        return self.root / "raw"

    @property
    def processed_dir(self) -> Path:
        """Rasters/vecteurs derives, alignes sur la grille gabarit
        (ndvi.tif, risk.tif, priorites.geojson, metadata.json...)."""
        return self.root / "processed"

    @property
    def outputs_dir(self) -> Path:
        """Visualisations de controle (PNG). Non servi au public."""
        return self.root / "outputs"

    def ensure_dirs(self) -> "CommuneContext":
        """Cree l'arborescence de la commune si elle n'existe pas.
        Retourne self pour permettre `ctx = CommuneContext("83130").ensure_dirs()`.
        """
        for d in (self.boundaries_dir, self.raw_dir, self.processed_dir, self.outputs_dir):
            d.mkdir(parents=True, exist_ok=True)
        return self

    # ------------------------------------------------------------------
    # Raccourcis de chemins (evite de repeter les `/ "..."` partout)
    # ------------------------------------------------------------------
    def processed(self, filename: str) -> Path:
        """Ex. ctx.processed("ndvi.tif") -> data/communes/83130/processed/ndvi.tif"""
        return self.processed_dir / filename

    def boundary(self, filename: str) -> Path:
        return self.boundaries_dir / filename

    def output(self, filename: str) -> Path:
        return self.outputs_dir / filename

    @property
    def metadata_path(self) -> Path:
        """Fiche de provenance (dates des donnees sources) de cette commune."""
        return self.processed_dir / "metadata.json"
