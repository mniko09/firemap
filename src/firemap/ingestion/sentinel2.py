"""[2] Sentinel-2 (CDSE, API) - mosaique la moins nuageuse + calcul NDVI/NDMI
cote serveur (evalscript), requete directement sur la grille gabarit (EPSG:2154)
pour eviter tout reechantillonnage supplementaire.
"""
import os
from typing import Tuple

import numpy as np
from dotenv import load_dotenv
from rasterio.transform import array_bounds
from sentinelhub import (
    BBox,
    CRS,
    DataCollection,
    MimeType,
    MosaickingOrder,
    SentinelHubRequest,
    SHConfig,
)

from .. import config
from ..grid import ReferenceGrid

EVALSCRIPT = """
//VERSION=3
function setup() {
  return { input: ["B04","B08","B11","SCL","dataMask"],
           output: { bands: 3, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) {
  // SCL: 3=ombre nuage, 8/9=nuages, 10=cirrus, 11=neige -> pixels invalides
  let valid = ![3,8,9,10,11].includes(s.SCL) && s.dataMask === 1;
  let ndvi = (s.B08 - s.B04) / (s.B08 + s.B04 + 1e-6);
  let ndmi = (s.B08 - s.B11) / (s.B08 + s.B11 + 1e-6);
  return [ndvi, ndmi, valid ? 1 : 0];
}
"""


def build_sh_config() -> SHConfig:
    load_dotenv(config.ROOT_DIR / ".env")
    sh_config = SHConfig()
    sh_config.sh_client_id = os.environ["CDSE_CLIENT_ID"]
    sh_config.sh_client_secret = os.environ["CDSE_CLIENT_SECRET"]
    sh_config.sh_base_url = "https://sh.dataspace.copernicus.eu"
    sh_config.sh_token_url = (
        "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
        "protocol/openid-connect/token"
    )
    return sh_config


def fetch_ndvi_ndmi(grid: ReferenceGrid, time_interval: Tuple[str, str]) -> np.ndarray:
    """Retourne un tableau (H, W, 3) = ndvi, ndmi, masque_valide (1=exploitable),
    deja aligne pixel-a-pixel sur la grille gabarit."""
    sh_config = build_sh_config()

    left, bottom, right, top = array_bounds(grid.height, grid.width, grid.transform)
    bbox = BBox([left, bottom, right, top], crs=CRS(int(grid.crs.to_epsg())))

    request = SentinelHubRequest(
        evalscript=EVALSCRIPT,
        input_data=[
            SentinelHubRequest.input_data(
                data_collection=DataCollection.SENTINEL2_L2A.define_from(
                    "s2l2a", service_url=sh_config.sh_base_url
                ),
                time_interval=time_interval,
                mosaicking_order=MosaickingOrder.LEAST_CC,
            )
        ],
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        bbox=bbox,
        size=(grid.width, grid.height),
        config=sh_config,
    )
    return request.get_data()[0]
