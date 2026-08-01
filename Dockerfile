FROM python:3.12
# Image complete (pas -slim) : rasterio/GDAL dependent dynamiquement de
# librairies systeme (libexpat, ...) absentes de l'image -slim.

WORKDIR /app

COPY requirements.txt pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir -e .

COPY web/ ./web/
COPY data/boundaries/ ./data/boundaries/
COPY data/processed/ ./data/processed/

EXPOSE 8000
CMD ["uvicorn", "firemap.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
