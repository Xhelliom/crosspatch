FROM python:3.12-slim

# git seul : `rsync` n'est plus utilisé depuis que `promote()` fait le miroir
# en Python. Une dépendance système de moins dans l'image.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Les dépendances avant le code : le cache de couche survit à chaque
# modification de source, et le worker reconstruit souvent.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Le conteneur exécute du code que des modèles réécrivent. Il tourne sans
# privilèges, avec un uid fixe pour que `fsGroup` puisse ouvrir les volumes.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin crosspatch \
    && mkdir -p /app/data /app/workspaces /app/candidates \
    && chown -R 10001:10001 /app
USER 10001

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
