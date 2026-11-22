FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    CONFIG_PATH=/config/config.yml \
    STATE_PATH=/data/state.json \
    RUN_MODE=loop

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config.example.yml /app/config.example.yml
COPY healthcheck.py /app/healthcheck.py
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV PYTHONPATH=/app/src

VOLUME ["/config", "/data"]

HEALTHCHECK --interval=5m --timeout=15s --start-period=60s \
  CMD python /app/healthcheck.py

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "bbwatch.cli"]
