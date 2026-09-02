// Renders every page in headless Chrome and writes a screenshot per route.
// A page that compiles can still fail to paint, so this fails on any console
// error, page error or response status of 400 or more.
//
//   node scripts/screenshots.mjs [base-url] [output-dir]
//
// Pass a run id in RUN_ID to capture the run view as well.

import { mkdir } from "node:fs/promises";
import { chromium } from "playwright-core";

// Enough to tell a rendered page from a blank one. A page with nothing to
// show still renders its header and an EmptyState, which clears this easily.
const MIN_TEXT = 60;

const base = process.argv[2] ?? "http://127.0.0.1:7100";
const out = process.argv[3] ?? "../files/screenshots";

const ROUTES = [
  ["dashboard", "/"],
  ["tests", "/tests"],
  ["tests-campaigns", "/tests?view=campaigns"],
  ["history", "/history"],
  ["units", "/units"],
  ["instruments", "/instruments"],
  ["system", "/system"],
];

await mkdir(out, { recursive: true });

const browser = await chromium.launch({ args: ["--no-sandbox"] });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });

const problems = [];
page.on("console", (message) => {
  if (message.type() === "error") problems.push(`${page.url()} console: ${message.text()}`);
});
page.on("pageerror", (error) => problems.push(`${page.url()} pageerror: ${error.message}`));
page.on("requestfailed", (request) => {
  // The run view holds an EventSource open; closing the browser aborts it.
  if (request.failure()?.errorText === "net::ERR_ABORTED") return;
  problems.push(`request failed: ${request.url()}`);
});
page.on("response", (response) => {
  if (response.status() >= 400) problems.push(`${response.status()} ${response.url()}`);
});

async function capture(name, route) {
  await page.goto(`${base}/#${route}`, { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForTimeout(3000);
  await page.screenshot({ path: `${out}/${name}.png`, fullPage: true });
  // A page paints its own `h1` through PageHeader, and only through it: the
  // error boundary renders an `h2`, and a view that painted nothing renders
  // neither. Counting characters instead would fail a page whose data is
  // legitimately empty, which is what an EmptyState is for.
  const painted = await page.evaluate(() => {
    const root = document.getElementById("root");
    return {
      heading: root?.querySelector("h1")?.textContent?.trim() ?? "",
      length: root?.innerText.length ?? 0,
    };
  });
  if (!painted.heading) problems.push(`${route} painted no page heading`);
  if (painted.length < MIN_TEXT) {
    problems.push(`${route} rendered only ${painted.length} characters`);
  }
  console.log(`${name}.png`);
}

/**
 * The profile editor, which is the tallest dialog the app opens.
 *
 * A modal that outgrows the viewport has nowhere to scroll — the backdrop is
 * fixed and the container is not bounded — so the settings below the fold
 * cannot be reached at all. Routing never reaches this, and jsdom cannot see
 * it, so it is opened and measured here.
 */
async function captureProfileEditor() {
  // `view` is asked for explicitly: the Tests page remembers the last one and
  // writes it back into the URL, so arriving without it lands on whichever
  // view the campaigns capture above left behind.
  await page.goto(`${base}/#/tests?view=suites`, { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForTimeout(2000);
  // A suite has to be picked before its profiles, and so the Edit buttons,
  // are on the page at all. Any suite will do; the dialog is the same shape.
  const suite = page.locator(".tests-page__rail-item").first();
  if ((await suite.count()) > 0) {
    await suite.click();
    await page.waitForTimeout(1500);
  }
  const edit = page.getByRole("button", { name: "Edit" }).first();
  if ((await edit.count()) === 0) {
    problems.push("no profile Edit control to open the tallest dialog with");
    return;
  }
  await edit.click();
  await page.waitForTimeout(2500);

  await page.screenshot({ path: `${out}/profile-editor.png`, fullPage: false });

  // Whether this particular suite's form is tall enough to overflow depends on
  // which suite sorts first, so the dialog is given content that certainly is
  // and then measured. A bounded dialog stays on screen and scrolls its body;
  // an unbounded one grows past the viewport, where the fixed backdrop leaves
  // no way to reach what is below the fold.
  const fit = await page.evaluate(() => {
    const dialog = document.querySelector(".profile-editor");
    const body = document.querySelector(".profile-editor__body");
    if (!dialog || !body) return null;
    const spacer = document.createElement("div");
    spacer.style.height = "4000px";
    spacer.style.flex = "0 0 auto";
    body.append(spacer);
    const box = dialog.getBoundingClientRect();
    const measured = {
      overflows: box.bottom > window.innerHeight || box.top < 0,
      scrolls: body.scrollHeight > body.clientHeight,
    };
    spacer.remove();
    return measured;
  });
  if (fit === null) {
    problems.push("profile editor did not open");
    return;
  }
  if (fit.overflows) problems.push("profile editor runs past the viewport when its form is tall");
  if (!fit.scrolls) problems.push("profile editor does not scroll a form taller than itself");
  console.log("profile-editor.png");
}

/** Each tab of the run view, which routing alone does not reach. */
async function captureRunTabs(runId) {
  await capture("run", `/runs/${encodeURIComponent(runId)}`);
  // Snapshots and Traces appear only on a run that recorded them, hence the
  // count check below.
  for (const tab of [
    "Overview",
    "Log",
    "Metrics",
    "Iterations",
    "Snapshots",
    "Traces",
    "Artifacts",
    "Notes",
  ]) {
    const control = page.getByRole("tab", { name: tab });
    if ((await control.count()) === 0) continue;
    await control.first().click();
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `${out}/run-${tab.toLowerCase()}.png`, fullPage: true });
    console.log(`run-${tab.toLowerCase()}.png`);
  }
}

for (const [name, route] of ROUTES) await capture(name, route);
await captureProfileEditor();
if (process.env.RUN_ID) await captureRunTabs(process.env.RUN_ID);

await browser.close();

if (problems.length > 0) {
  console.error("\n" + [...new Set(problems)].join("\n"));
  process.exit(1);
}
console.log("\nno console errors, no failed requests");
