#!/usr/bin/env bash
# Regenerate the UI screenshots in docs/assets/ui from the offline demo.
#
#   scripts/ui_previews/run.sh            # needs uv, Node 20+, and Docker (or Playwright's Chromium)
#
# Builds the web app, sets up the demo in a throwaway ALC_HOME, starts `alchemist serve` with the scripted model on
# port 8123 (ALC_PREVIEW_PORT), walks it with shoot.mjs, and copies the screens that changed into docs/assets/ui
# (sync.py). Nothing outside the temporary folder and docs/assets/ui is touched.
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
here="$root/scripts/ui_previews"
port="${ALC_PREVIEW_PORT:-8123}"
work="$(mktemp -d)"
trap 'kill "${server:-0}" 2>/dev/null || true; rm -rf "$work"' EXIT

cd "$root"
if [[ "${SKIP_WEB_BUILD:-}" != 1 ]]; then (cd web && npm ci --no-audit --no-fund && npm run build); fi
(cd "$here" && npm ci --no-audit --no-fund)

export ALC_HOME="$work/home" ALC_PREVIEW_HOLD="$work/hold"
# The Provenance tab shows the Python version, so every machine runs the demo on the same minor version, in its own
# environment so the project's .venv is left alone.
export UV_PROJECT_ENVIRONMENT="$work/venv" UV_PYTHON="${ALC_PREVIEW_PYTHON:-3.12}"
uv run alchemist demo setup > "$work/setup.log"
touch "$ALC_PREVIEW_HOLD"

PYTHONPATH="$here${PYTHONPATH:+:$PYTHONPATH}" ALC_LAUNCHER=preview_launcher:PreviewLauncher ALC_RECONCILE_SECONDS=0 \
  uv run alchemist serve --scripted --port "$port" > "$work/server.log" 2>&1 &
server=$!
for _ in $(seq 60); do curl -fs "http://127.0.0.1:$port/api/health" -o /dev/null && break; sleep 0.5; done

# The browser runs in Playwright's own image when Docker is available, so every machine, CI included, renders
# the same pixels. ALC_PREVIEW_DOCKER=0 uses the local Chromium instead; its images may differ slightly in text
# rendering and show up as changed.
export ALC_URL="http://127.0.0.1:$port" OUT="$work/shots"
image=mcr.microsoft.com/playwright:v1.56.1-noble
if [[ "${ALC_PREVIEW_DOCKER:-auto}" != 0 ]] && docker info > /dev/null 2>&1; then
  shoot=(docker run --rm --network host --user "$(id -u):$(id -g)" -e HOME=/tmp -e ALC_URL -e OUT -e ALC_PREVIEW_HOLD
         -v "$here:$here:ro" -v "$work:$work" -w "$here" "$image" node shoot.mjs)
elif [[ "${ALC_PREVIEW_DOCKER:-auto}" == 1 ]]; then
  echo "ALC_PREVIEW_DOCKER=1 but Docker is not available" >&2; exit 1
else
  echo "Docker is not available: using the local Chromium, whose text rendering may differ from CI's" >&2
  shoot=(node "$here/shoot.mjs")
fi
if ! "${shoot[@]}"; then
  echo "--- server log"; tail -n 80 "$work/server.log"; exit 1
fi
uv run python "$here/sync.py" "$work/shots" "$root/docs/assets/ui"
