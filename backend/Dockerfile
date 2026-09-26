FROM python:3.13-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

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

RUN useradd --create-home --shell /bin/bash appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/system/health', timeout=3)" || exit 1

# Applies any pending schema migrations before the API starts serving traffic.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
