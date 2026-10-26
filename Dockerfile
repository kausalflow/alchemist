# One image for the CLI, the API with the web app, the source sync, the workers and the sandbox kernel. The AWS stack
# (infra/), docker compose (deploy/compose/) and the Helm chart (deploy/helm/) run it with different commands.
# Production on AWS splits this into an orchestrator image and a sandbox image, each pinned by digest.

# The web app, built once and served by `alchemist serve`.
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
RUN npm run build

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
# System packages a plugin or connector needs, e.g. APT_PACKAGES="gcc libkrb5-dev krb5-user" for Kerberos sign-in to
# the PI Web API, together with EXTRA_PACKAGES="penguineer[pi-kerberos] pyspnego[kerberos]".
ARG APT_PACKAGES=""
RUN if [ -n "$APT_PACKAGES" ]; then apt-get update && apt-get install -y --no-install-recommends $APT_PACKAGES \
    && rm -rf /var/lib/apt/lists/*; fi
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# A fixed uid, so Kubernetes can check runAsNonRoot.
RUN uv sync --locked --no-dev && useradd -m -u 10001 alchemist && mkdir -p /data && chown alchemist /data
# Plugins (docs/guides/plugins.md) installed next to alchemist, e.g. --build-arg EXTRA_PACKAGES="acme-alchemist==1.2.0".
# Paths are relative to the build context (the repository root). The infrastructure sets this from plugins.packages.
ARG EXTRA_PACKAGES=""
COPY . /tmp/build-context
RUN if [ -n "$EXTRA_PACKAGES" ]; then cd /tmp/build-context && uv pip install --python /app/.venv/bin/python $EXTRA_PACKAGES; fi \
    && rm -rf /tmp/build-context
COPY --from=web /web/dist /app/web/dist
ENV PATH="/app/.venv/bin:$PATH" ALC_HOME=/data ALC_WEB_DIST=/app/web/dist
USER 10001
# The sandbox has no internet, so DuckDB's S3 extensions ship in the image (aws: credential_process credentials).
RUN python -c "import duckdb; duckdb.connect().execute('install httpfs; install aws')"
EXPOSE 8000
ENTRYPOINT ["alchemist"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
