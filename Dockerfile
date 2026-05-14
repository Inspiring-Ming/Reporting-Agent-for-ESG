# AMP AI Expo demo — single-stage Python image.
# Built for Render/Fly/Railway one-click deploys.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=5050

WORKDIR /app

# System deps: gcc + libpangoft2 stripped — reportlab is pure Python so we
# don't need pango anymore.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Source + data
COPY app/ ./app/
COPY templates/ ./templates/
COPY static/ ./static/
COPY data/ ./data/

# Render/Railway/Fly all set $PORT — gunicorn reads it. Two workers is plenty
# for booth traffic; bigger numbers blow up memory because SQLite + materiality
# is loaded per worker.
EXPOSE 5050
CMD gunicorn --workers 2 --threads 4 --timeout 120 \
    --bind 0.0.0.0:${PORT} "app.server:create_app()"
