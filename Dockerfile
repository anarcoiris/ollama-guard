FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY guard/ ./guard/
COPY config/ ./config/

ENV GUARD_DEFAULTS=/app/config/guard.defaults.yaml
ENV GUARD_BACKENDS_MODE=docker
ENV GUARD_LANE_MIRRORS=1
ENV GUARD_METRICS_PATH=/logs/metrics.jsonl
ENV PYTHONUNBUFFERED=1

EXPOSE 11800 11434 11435 11436 11437

CMD ["python", "-m", "guard"]
