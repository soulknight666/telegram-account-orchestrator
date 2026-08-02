FROM python:3.13-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY . /src
RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/python -m pip install --no-cache-dir ".[bot]"

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    TAM_ENV_FILE=/config/tao.env \
    TAM_DATA_DIR=/data \
    TAM_DEPLOY=server \
    TAM_FRONTEND=web \
    TAM_HOST=0.0.0.0 \
    TAM_PORT=8848

RUN groupadd --gid 10001 tao \
    && useradd --uid 10001 --gid tao --create-home --shell /usr/sbin/nologin tao \
    && mkdir -p /app /config /data \
    && chown -R tao:tao /app /config /data

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=tao:tao . /app

USER tao
EXPOSE 8848
VOLUME ["/config", "/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8848/', timeout=3).read(1)"

# Runtime entry: python -m tam.run
CMD ["python", "-m", "tam.run", "--deploy", "server", "--frontend", "web", "--host", "0.0.0.0", "--port", "8848", "--no-menu"]
