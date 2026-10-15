# Contributing

## Development setup

```bash
uv sync --extra dev
uv run pytest -q
```

CI runs the test suite and the offline demo end to end (`alchemist demo setup && alchemist demo run`) on every pull
request. A change that touches the run loop should keep the demo's replay at zero mismatches.

## Working on the web app

The web app is in `web/` (React, TypeScript, Vite). Run the API and the dev server together:

```bash
uv run alchemist serve --scripted      # API on :8000, offline demo model
cd web && npm ci && npm run dev        # http://localhost:5173, /api forwarded to :8000
```

`npm run build` type-checks and writes `web/dist`, which `alchemist serve` then serves. CI runs the build. Every call
to the API goes through `web/src/api.ts`; keep its types in step with `src/alchemist/api/`.

### UI screenshots

The [UI tour](ui-tour.md) shows screenshots from `docs/assets/ui`. When you change what a screen shows, regenerate them
and commit the images that changed:

```bash
scripts/ui_previews/run.sh      # needs uv, Node 20+ and Docker
```

The browser runs in Playwright's Docker image (`mcr.microsoft.com/playwright`, the same version as the `playwright`
package), with the app's fonts served from `@fontsource` instead of Google Fonts, so a laptop and CI produce the same
pixels. Without Docker, `ALC_PREVIEW_DOCKER=0` uses a local Chromium; its text rendering can differ slightly, which
marks every image as changed.

The script builds the web app, sets up the demo in a temporary folder, starts `alchemist serve --scripted` on port
8123 (`ALC_PREVIEW_PORT`) and walks it with Playwright (`scripts/ui_previews/shoot.mjs`): ask, approve the plan, a
run held part-way (`preview_launcher.py`), the sealed report and its tabs, a replay, the catalog and every admin page,
each in light and dark. Run ids, times, step durations and the Python patch version in API responses are swapped for fixed stand-ins, and `sync.py` keeps an
existing image unless more than 0.05% of its pixels changed (`ALC_PREVIEW_THRESHOLD`), so only screens that really
changed show up in the diff, where GitHub's image view shows the before and after. To add a screen, add a `shoot()`
call to `shoot.mjs` and the image to `docs/ui-tour.md`.

The UI previews workflow does the same on pull requests that touch the web app or the API, uploads the screenshots as
the `ui-previews` artifact and warns when the committed images are out of date.

## Working on the documentation

The site is built with [Zensical](https://zensical.org). Its configuration is `zensical.toml` at the repository root
and the pages are in `docs/`.

```bash
uvx zensical serve    # live preview on http://localhost:8000
uvx zensical build    # static site in ./site
```

When you add a page, add it to `nav` in `zensical.toml`. Diagrams are written as fenced `mermaid` blocks.

Keep pages close to the code: when you change a command, an environment variable, a run file or a tool, update the
matching page under `docs/reference/`.

## Conventions

- All file access goes through `alchemist.paths` (cloudpathlib), so code works on S3 and locally.
- Every YAML and JSON file Alchemist writes carries a `schema` field such as `alchemist.manifest/v1`.
- Numbers that end up in a report come from recorded findings, never from text the model wrote.
- Prompt and tool-definition changes change the prompt hash recorded in manifests; mention them in the pull request.
