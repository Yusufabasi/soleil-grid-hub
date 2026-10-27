# The provider double as a container (compose service `provider-stub`).
#
# Built from the package, not from a mounted checkout: `pip install` resolves `httpx` from the
# package metadata, so the image cannot quietly run with a dependency the pyproject does not name.
# The build context is the repository root, which is what lets the wheel be built from
# `tools/provider_stub` while the tag stays `soleil/provider-stub`.
#
# No secrets are baked in: every `STUB_SECRET_*` and the control token arrive as environment
# variables from the compose service, and `config.load()` refuses to start without them.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# The package and its metadata only: no tests, no schemas, no journal.
COPY tools/provider_stub/pyproject.toml /app/tools/provider_stub/pyproject.toml
COPY tools/provider_stub/src /app/tools/provider_stub/src
RUN pip install --no-cache-dir /app/tools/provider_stub

# The journal lives on a volume so a restart replays it (that is the whole point of the file).
RUN mkdir -p /var/lib/provider_stub && chown -R 65532:65532 /var/lib/provider_stub

# A non-root numeric user, as every image in this repository runs as one.
USER 65532:65532

EXPOSE 9100

# `--port` is a CLI flag, so the service can be moved off 9100 without editing the image.
ENTRYPOINT ["python", "-m", "provider_stub"]
CMD ["serve", "--port", "9100"]
