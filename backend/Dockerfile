FROM python:3.13-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Debian security fixes land in the archive before the python:3.13-slim tag is rebuilt; upgrading
# the OS packages here keeps the image scanner's fixed-CVE gate green between base-image releases
# (as `apk upgrade` does for the frontend image).
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# requirements first so dependency installs are cached independently of app code changes
COPY requirements.txt .
# pip is only needed to install; the running container must not install anything. Removing it
# also removes the packages pip vendors for its own use (setuptools, msgpack, ...), which are
# what image scanners flag as unfixable-from-requirements HIGH findings.
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m pip uninstall -y pip \
    && rm -rf /root/.cache/pip

COPY app ./app
COPY alembic.ini .
COPY alembic ./alembic
COPY scripts ./scripts

RUN useradd --create-home --shell /bin/bash appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/system/health', timeout=3)" || exit 1

# Applies any pending schema migrations, then serves with API_WORKERS uvicorn processes (default 1; LD-7). Behind Caddy
# the client IP comes from X-Forwarded-For, only from the proxies named in FORWARDED_ALLOW_IPS (P0.2 / S1) - see the script.
CMD ["sh", "scripts/serve_api.sh"]
