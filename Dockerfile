# One image for the MVP: the CLI, the orchestrator and the sandbox kernel.
# Production splits this into an orchestrator image and a sandbox image, each pinned by digest.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev && useradd -m alchemist
# Plugins (docs/guides/plugins.md) installed next to alchemist, e.g. --build-arg EXTRA_PACKAGES="acme-alchemist==1.2.0".
# Paths are relative to the build context (the repository root). The infrastructure sets this from plugins.packages.
ARG EXTRA_PACKAGES=""
COPY . /tmp/build-context
RUN if [ -n "$EXTRA_PACKAGES" ]; then cd /tmp/build-context && uv pip install --python /app/.venv/bin/python $EXTRA_PACKAGES; fi \
    && rm -rf /tmp/build-context
ENV PATH="/app/.venv/bin:$PATH" ALC_HOME=/data
USER alchemist
# The sandbox has no internet, so DuckDB's S3 extensions ship in the image (aws: credential_process credentials).
RUN python -c "import duckdb; duckdb.connect().execute('install httpfs; install aws')"
ENTRYPOINT ["alchemist"]
