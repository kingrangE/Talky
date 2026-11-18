FROM node:24-slim AS frontend
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libsndfile1 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY requirements-google-tts.txt .
RUN pip install -r requirements-google-tts.txt

COPY . .
COPY --from=frontend /web/dist ./frontend/dist

RUN chmod +x docker/app-entrypoint.sh

EXPOSE 8501

ENTRYPOINT ["bash", "docker/app-entrypoint.sh"]
