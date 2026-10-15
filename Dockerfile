# One image for the MVP: the CLI, the orchestrator and the sandbox kernel.
# Production splits this into an orchestrator image and a sandbox image, each pinned by digest.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev && useradd -m alchemist
ENV PATH="/app/.venv/bin:$PATH" ALC_HOME=/data
USER alchemist
# The sandbox has no internet, so DuckDB's S3 extension ships in the image.
RUN python -c "import duckdb; duckdb.connect().execute('install httpfs')"
ENTRYPOINT ["alchemist"]
