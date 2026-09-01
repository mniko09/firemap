"""http.py -- une session HTTP partagee, avec retries et backoff.

Les sources du pipeline (geo.api.gouv.fr, IGN WMS/WFS, Meteo-France DPClim,
Georisques, education.gouv.fr) tombent regulierement en timeout passager. Sans
retry, une seule coupure fait echouer toute la generation d'une commune.

Politique : 3 tentatives, backoff 0 s / 2 s / 4 s, sur les erreurs de connexion,
les timeouts de lecture et les codes 429 / 500 / 502 / 503 / 504. GET et POST
(les appels du pipeline sont idempotents cote serveur -- commandes de fichiers,
lectures).
"""
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Timeout par defaut (connexion, lecture) si l'appelant n'en passe pas.
DEFAULT_TIMEOUT = (10, 60)

_retry = Retry(
    total=3, connect=3, read=3, status=3,
    backoff_factor=1,                       # attentes : 0 s, 2 s, 4 s
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(("GET", "POST")),
    raise_on_status=False,
)


class _TimeoutSession(requests.Session):
    """Session qui applique DEFAULT_TIMEOUT quand aucun timeout n'est fourni."""

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
        return super().request(method, url, **kwargs)


def _build_session() -> requests.Session:
    s = _TimeoutSession()
    adapter = HTTPAdapter(max_retries=_retry, pool_connections=10, pool_maxsize=10)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


# Singleton : thread-safe pour un usage GET/POST simple (requests.Session l'est
# pour l'envoi de requetes independantes).
SESSION: requests.Session = _build_session()
