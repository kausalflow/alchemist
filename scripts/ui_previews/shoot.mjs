// Walks the scripted demo through the web app and saves a light and a dark screenshot of each screen.
// Run it through run.sh, which starts the server; this file only drives the browser.
//
//   ALC_URL      the server (default http://127.0.0.1:8123)
//   ALC_PREVIEW_HOLD   the file that holds the analysis part-way (see preview_launcher.py); removed here
//   OUT          where the PNGs go (default docs/assets/ui)

import { chromium } from "playwright";
import { existsSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

const BASE = process.env.ALC_URL ?? "http://127.0.0.1:8123";
const OUT = process.env.OUT ?? "docs/assets/ui";
const HOLD = process.env.ALC_PREVIEW_HOLD;
const VIEWPORT = { width: 1440, height: 900 };
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch(
  process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {},
);
const shots = [];

// Every demo run has new ids and timestamps. So that a screen changes only when the app does, API responses are
// rewritten on the way to the browser: ids become stable stand-ins (in order of first sight, which the fixed flow
// keeps the same), timestamps near now (ISO strings, or epoch seconds) move to FIXED rounded to the hour, step
// durations become 0.1 s, and the Python patch version is dropped. Requests map the stand-ins back. The live step
// stream of a running run is replaced by one built from its steps so far, so it gets the same treatment.
const FIXED = Date.parse("2026-09-01T09:00:00Z");
const START = Date.now();
const ULID = /\b[0-9A-HJKMNP-TV-Z]{26}\b/g;
const ISO = /\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?/g;
const EPOCH = /(?<=[:[,]\s*)\d{10}(?:\.\d+)?(?=\s*[,}\]])/g; // transcript times are epoch seconds in JSON
const HOUR = 3_600_000;
const toFake = new Map();
const toReal = new Map();

function fakeId(id) {
  if (!toFake.has(id)) {
    const fake = `01DEMO${String(toFake.size + 1).padStart(20, "0")}`;
    toFake.set(id, fake);
    toReal.set(fake, id);
  }
  return toFake.get(id);
}

const near = (t) => !isNaN(t) && t > START - 86_400_000 && t < START + 60 * 86_400_000;
const moved = (t) => FIXED + Math.round((t - START) / HOUR) * HOUR;

function steady(text) {
  return text
    .replace(EPOCH, (n) => {
      const t = Number(n) * 1000;
      return near(t) ? String(moved(t) / 1000) : n;
    })
    .replace(/("seconds":\s*)\d+(?:\.\d+)?/g, (_, key) => `${key}0.1`)
    .replace(/("python":\s*"\d+\.\d+)\.\d+"/g, '$1"') // patch releases differ between machines
    .replace(ULID, fakeId)
    .replace(ISO, (iso) => {
      const t = Date.parse(/(?:Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`);
      return near(t) ? new Date(moved(t)).toISOString() : iso;
    });
}

const real = (url) => url.replace(/01DEMO\d{20}/g, (f) => toReal.get(f) ?? f);

// The app loads IBM Plex from Google Fonts. Serve the same fonts from @fontsource instead and keep everything else
// off the network, so a screenshot does not depend on the network or on the machine's installed fonts.
const require = createRequire(import.meta.url);
const FONTS = [
  ["IBM Plex Sans", "ibm-plex-sans", [400, 500, 600, 700]],
  ["IBM Plex Mono", "ibm-plex-mono", [400, 500]],
];
const fontDir = (pkg) => join(dirname(require.resolve(`@fontsource/${pkg}/package.json`)), "files");
const FONT_CSS = FONTS.flatMap(([family, pkg, weights]) =>
  weights.flatMap((w) =>
    ["latin", "latin-ext"].map(
      (subset) =>
        `@font-face{font-family:"${family}";font-style:normal;font-weight:${w};font-display:block;` +
        `src:url(https://fonts.local/${pkg}/${pkg}-${subset}-${w}-normal.woff2) format("woff2")}`,
    ),
  ),
).join("\n");

async function offline(route) {
  const url = new URL(route.request().url());
  if (url.host === "fonts.googleapis.com") return route.fulfill({ contentType: "text/css", body: FONT_CSS });
  if (url.host === "fonts.local") {
    const [, pkg, file] = url.pathname.split("/");
    return route.fulfill({ contentType: "font/woff2", body: readFileSync(join(fontDir(pkg), file)) });
  }
  return route.abort();
}

async function steadyRoute(route) {
  const url = real(route.request().url());
  if (url.endsWith("/events")) {
    const base = url.slice(0, -"/events".length);
    const run = await (await route.fetch({ url: base })).json();
    const steps = await (await route.fetch({ url: `${base}/steps` })).json();
    const sse = (event, data) => `event: ${event}\ndata: ${steady(JSON.stringify(data))}\n\n`;
    const body = [
      sse("status", { status: run.run.status }),
      ...["planning", "analysis"].flatMap((phase) => steps[phase].map((s) => sse("step", { phase, ...s }))),
    ].join("");
    return route.fulfill({ status: 200, contentType: "text/event-stream", body });
  }
  const res = await route.fetch({ url });
  const type = res.headers()["content-type"] ?? "";
  if (!/json|text|html|javascript/.test(type)) return route.fulfill({ response: res });
  return route.fulfill({ response: res, body: steady(await res.text()) });
}

async function session(user) {
  const ctx = await browser.newContext({ viewport: VIEWPORT, reducedMotion: "reduce", locale: "en-GB", timezoneId: "UTC" });
  if (user) await ctx.addCookies([{ name: "alc_user", value: user, url: BASE }]);
  await ctx.route((url) => !url.href.startsWith(BASE), offline);
  await ctx.route(`${BASE}/api/**`, steadyRoute);
  const page = await ctx.newPage();
  await page.clock.setFixedTime(FIXED + 10 * 60_000);
  page.on("pageerror", (e) => console.error(`page error: ${e.message}`));
  return page;
}

async function api(page, method, path, data) {
  const res = await page.request.fetch(`${BASE}/api${real(path)}`, { method, data });
  if (!res.ok()) throw new Error(`${method} ${path}: ${res.status()} ${await res.text()}`);
  return res.json();
}

async function until(what, fn, timeout = 60_000) {
  const end = Date.now() + timeout;
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, 300));
  }
}

const status = async (page, id) => (await api(page, "GET", `/runs/${id}`)).run.status;

async function settle(page) {
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForFunction(() => !document.body.innerText.includes("Loading…"), null, { timeout: 15_000 }).catch(() => {});
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(400);
}

/** Save `name`-light.png and `name`-dark.png of what the page shows now. */
async function shoot(page, name, { fullPage = false } = {}) {
  for (const scheme of ["light", "dark"]) {
    await page.emulateMedia({ colorScheme: scheme });
    await settle(page);
    await page.mouse.move(VIEWPORT.width - 1, VIEWPORT.height - 1);
    await page.screenshot({ path: `${OUT}/${name}-${scheme}.png`, fullPage, animations: "disabled", caret: "hide" });
  }
  await page.emulateMedia({ colorScheme: null });
  shots.push(name);
  console.log(`  ${name}`);
}

async function open(page, path) {
  await page.goto(`${BASE}${path}`);
  await settle(page);
}

/** Open a report tab and scroll it to the top, so the shot shows the tab's content. */
async function tab(page, label) {
  const t = page.getByRole("tab", { name: label });
  await t.click();
  await settle(page);
  await t.evaluate((el) => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 24));
}

// ---------------------------------------------------------------- the asker's flow

const anon = await session(null);
await open(anon, "/");
await shoot(anon, "sign-in");
await anon.context().close();

const eng = await session("eng_p1");

await open(eng, "/ask");
await eng.getByRole("button", { name: "Use the demo question" }).click();
await eng.getByLabel("Project").fill("line2-quality");
await shoot(eng, "ask");
await eng.locator("button.primary.lg").click();
await eng.waitForURL(/\/runs\/[^/]+$/);
const runId = eng.url().split("/").pop();

await until("the plan", async () => (await status(eng, runId)) === "awaiting_approval");
await eng.reload();
await shoot(eng, "approval-gate");

await open(eng, "/");
await shoot(eng, "board-needs-approval");

await api(eng, "POST", `/runs/${runId}/plan:approve`, {});
await until("the first findings", async () => {
  const steps = await api(eng, "GET", `/runs/${runId}/steps`);
  return JSON.stringify(steps).includes("record_finding");
});
await open(eng, `/runs/${runId}`);
await shoot(eng, "running");
if (HOLD && existsSync(HOLD)) rmSync(HOLD);

await until("the sealed report", async () => ["completed", "partial"].includes(await status(eng, runId)), 120_000);
await open(eng, `/runs/${runId}`);
await shoot(eng, "report");
await tab(eng, "Data");
await shoot(eng, "report-data");
await tab(eng, "Methods and plan");
await shoot(eng, "report-methods");
await tab(eng, "Notebook");
await shoot(eng, "report-notebook");
await tab(eng, "Provenance");
await shoot(eng, "report-provenance");

const replay = await api(eng, "POST", `/runs/${runId}:replay`, { mode: "replay" });
const replayId = replay.run_id ?? replay.run?.run_id;
await until("the replay", async () => !["queued", "running", "replaying", "planning"].includes(await status(eng, replayId)), 120_000);
await open(eng, `/runs/${replayId}`);
await shoot(eng, "replay");

await open(eng, "/");
await shoot(eng, "board");
await open(eng, "/runs");
await shoot(eng, "reports");
await open(eng, "/catalog");
await shoot(eng, "catalog");
const first = eng.locator('main a[href^="/catalog/"]').first();
await first.click();
await settle(eng);
await shoot(eng, "dataset");
await open(eng, "/about");
await shoot(eng, "about");
await eng.context().close();

// ---------------------------------------------------------------- the admin area

const admin = await session("data_admin");
await open(admin, "/admin");
await shoot(admin, "admin");
for (const [path, name] of [
  ["", "admin-plant-overview"],
  ["/data", "admin-plant-data"],
  ["/knowledge", "admin-plant-knowledge"],
  ["/lines", "admin-plant-lines"],
]) {
  await open(admin, `/admin/plants/P01${path}`);
  await shoot(admin, name);
}
await open(admin, "/admin/plants/global/knowledge");
await shoot(admin, "admin-shared-knowledge");
for (const page of ["workspaces", "access", "models"]) {
  await open(admin, `/admin/${page}`);
  await shoot(admin, `admin-${page}`);
}

await browser.close();
console.log(`${shots.length} screens, ${shots.length * 2} images in ${OUT}`);
