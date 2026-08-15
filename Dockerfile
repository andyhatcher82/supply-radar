FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY supply_radar/ ./supply_radar/
COPY static/ ./static/
COPY config/ ./config/
# The published snapshot the console serves. Without this the deployed app
# loads an empty shell, which is exactly how a demo fails in front of people.
COPY snapshot/ ./snapshot/

ENV PORT=8080
EXPOSE 8080

CMD exec uvicorn supply_radar.api.main:app --host 0.0.0.0 --port ${PORT}
