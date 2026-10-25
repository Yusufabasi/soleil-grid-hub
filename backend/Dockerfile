# Soleil Grid Hub backend image: one image, several process types (docs/09 2.5).
#
# Stage 1 installs the locked dependency set into /opt/deps with hashes checked; stage 2 is
# the runtime and carries no build tool, no pip cache and no source metadata. The base image
# is pinned by digest so a rebuild cannot silently pick up a different interpreter.
#
# The build context is the repository root, because the image carries db/ (the SQL authority,
# ADR-0020) next to src/ and sets HUB_REPO_ROOT=/app so `manage.py migrate` finds it.
#
#   docker build -f backend/Dockerfile -t soleil/hub:dev .
#
# No ENTRYPOINT: compose names each process type explicitly, which is the same thing an
# orchestrator's `command` does. The drain wrapper is a path in the image, used by the WSGI
# pools only.

FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    UV_CACHE_DIR=/tmp/uv-cache

# uv resolves and exports the lock file; its version is pinned like any other dependency. It
# comes from PyPI rather than its own registry so the build depends on one index, not two.
RUN pip install --no-cache-dir "uv==0.13.5"

WORKDIR /build
COPY backend/pyproject.toml backend/uv.lock ./
# Hashes are in the export by default (uv >= 0.10), so pip installs in hash-checking mode:
# a substituted or tampered wheel fails the build.
RUN uv export --frozen --no-dev --no-emit-project --output-file /build/requirements.txt \
 && python -m pip install --no-cache-dir --no-compile --target /opt/deps -r /build/requirements.txt \
 && find /opt/deps -name '*.dist-info' -prune -o -name '__pycache__' -type d -exec rm -rf {} +


FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS runtime

# Non-root from the first instruction that touches the filesystem: the image never needs
# write access to itself, and compose mounts a tmpfs at /tmp for the drain flag.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin --home-dir /app hub

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/deps:/app/src \
    PATH=/opt/deps/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \
    HUB_REPO_ROOT=/app \
    DJANGO_SETTINGS_MODULE=hub.settings.api

WORKDIR /app

COPY --from=builder /opt/deps /opt/deps
COPY --chown=10001:10001 backend/manage.py /app/manage.py
COPY --chown=10001:10001 backend/src /app/src
COPY --chown=10001:10001 backend/deploy /app/deploy
COPY --chown=10001:10001 db /app/db

RUN chmod 0555 /app/deploy/entrypoint-drain.sh

USER 10001
EXPOSE 8000 9102

# The metrics port is the one signal every process type opens (gunicorn's when_ready, the
# ASGI entrypoint and the worker commands all call start_metrics_server), so it is the honest
# default probe. Compose replaces it with readiness where readiness is the right answer.
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:9102/metrics', timeout=2)"

CMD ["gunicorn", "hub.wsgi_api:application", "-c", "deploy/gunicorn.py"]
