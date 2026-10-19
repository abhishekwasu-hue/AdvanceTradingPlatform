# AMW AI Custom Stock Screener - production image (dashboard + API share this image)
# Includes every Strategy Lab runtime: Python, Node.js 22 + TypeScript (JavaScript/TypeScript), R, Lua (lupa), SQLite.

FROM node:22-slim AS node
RUN npm install -g typescript@6.0.2 && npm cache clean --force

FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# r-base-core: R runtime for R strategies. unshare/setpriv (util-linux, in the base image) power the Lab sandbox.
RUN apt-get update && apt-get install -y --no-install-recommends r-base-core \
    && rm -rf /var/lib/apt/lists/* && which unshare setpriv Rscript
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/typescript /usr/local/lib/node_modules/typescript
RUN node --version && node -e "console.log('typescript', require('/usr/local/lib/node_modules/typescript').version)"

WORKDIR /opt/amw
COPY requirements.txt requirements-postgres.txt ./
RUN pip install -r requirements-postgres.txt
COPY . .
RUN mkdir -p data .cache .sandbox && chmod 700 data .cache && chmod 711 .sandbox && chmod -R o+rX /opt/amw/src

# The sandbox needs root + CAP_SYS_ADMIN *inside* the container to create network/mount namespaces and then drops
# every strategy process to uid 65534. See docs/SECURITY.md for the trade-offs.
EXPOSE 8501 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request as u;u.urlopen('http://localhost:8501/_stcore/health')" || python -c "import urllib.request as u;u.urlopen('http://localhost:8000/health/live')"
CMD ["python", "-m", "streamlit", "run", "Home.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
