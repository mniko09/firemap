FROM python:3.12
# Image complete (pas -slim) : rasterio/GDAL dependent dynamiquement de
# librairies systeme (libexpat, ...) absentes de l'image -slim.

ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1

WORKDIR /app

COPY requirements.txt pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir -e .

COPY web/ ./web/
COPY data/priority_communes.json ./data/priority_communes.json
# data/communes/, data/firemap.sqlite, data/raw/ : generes au runtime (volume).
# .env : injecte au runtime (variables d'environnement ou secret monte), jamais dans l'image.

EXPOSE 8000
# --workers 1 IMPERATIF : le planificateur APScheduler tourne in-process ; avec
# plusieurs workers il y aurait autant de planificateurs (scans dupliques).
CMD ["uvicorn", "firemap.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
