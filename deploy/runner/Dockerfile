# Runner S6: the ATP runner image (docs/design/USER_RUNNER.md section 3 (a), step S6).
#
# Built ONLY from the context `python deploy/runner/build_context.py --out <dir>` writes: the runner's module set from
# backend/app/runner/manifest.py, the data files of those packages and deploy/runner/requirements.lock. The private
# package, its loaders, AI, admin, marketplace, Lab, the server's worker / web app, billing and the lake are never in
# that context, and the check below fails the build if any of them is.
#
#     python deploy/runner/build_context.py --out /tmp/runner-ctx && docker build -t atp-runner /tmp/runner-ctx
#
# The running container: a non-root user, no inbound port (the runner only connects out: his broker, later ATP),
# everything it keeps in the /runner volume (runner.db, audit.jsonl, the LIVE / KILL switch files, runner.json and,
# with step S5, the keystore), a healthcheck that reads whether the runner process holds that folder.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/runner \
    ATP_RUNNER_HOME=/runner

WORKDIR /opt/runner

# Debian security fixes land before the base tag is rebuilt (the same step as backend/Dockerfile).
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Only the runner's packages, every one pinned and hash-checked; pip is removed after (nothing installs at run time).
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && python -m pip uninstall -y pip \
    && rm -rf /root/.cache/pip

COPY app ./app
COPY FORBIDDEN.txt MODULES.txt ./

# The image checks itself: no forbidden package is present or importable, and the runner's entry point imports.
RUN python -c "import importlib.util, pathlib, sys; \
bad = [p for p in pathlib.Path('FORBIDDEN.txt').read_text().split() \
       if pathlib.Path(*p.split('.')).exists() or pathlib.Path(*p.split('.')).with_suffix('.py').exists() \
       or (p.count('.') == 0 and importlib.util.find_spec(p) is not None)]; \
sys.exit('forbidden in the runner image: ' + ', '.join(bad)) if bad else None" \
    && python -c "import app.runner.__main__, app.runner.manifest"

RUN useradd --uid 10001 --user-group --no-create-home --home-dir /runner --shell /usr/sbin/nologin runner \
    && mkdir -p /runner && chown runner:runner /runner && chmod 700 /runner
USER runner
VOLUME ["/runner"]

HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "-m", "app.runner", "health", "--home", "/runner"]

ENTRYPOINT ["python", "-m", "app.runner"]
CMD ["run", "--home", "/runner"]
