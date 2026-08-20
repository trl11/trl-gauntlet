// Renders every page in headless Chrome and writes a screenshot per route.
// A page that compiles can still fail to paint, so this fails on any console
// error, page error or response status of 400 or more.
//
//   node scripts/screenshots.mjs [base-url] [output-dir]
//
// Pass a run id in RUN_ID to capture the run view as well.

import { mkdir } from "node:fs/promises";
import { chromium } from "playwright-core";

const base = process.argv[2] ?? "http://127.0.0.1:7100";
const out = process.argv[3] ?? "../files/screenshots";

const ROUTES = [
  ["dashboard", "/"],
  ["tests", "/tests"],
  ["tests-campaigns", "/tests?view=campaigns"],
  ["history", "/history"],
  ["units", "/units"],
  ["instruments", "/instruments"],
  ["settings", "/settings"],
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
  const text = await page.evaluate(() => document.getElementById("root")?.innerText ?? "");
  if (text.length < 200) problems.push(`${route} rendered only ${text.length} characters`);
  console.log(`${name}.png`);
}

/** Each tab of the run view, which routing alone does not reach. */
async function captureRunTabs(runId) {
  await capture("run", `/runs/${encodeURIComponent(runId)}`);
  // Snapshots appears only on a run that wrote images, hence the count check below.
  for (const tab of [
    "Overview",
    "Log",
    "Metrics",
    "Iterations",
    "Snapshots",
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
if (process.env.RUN_ID) await captureRunTabs(process.env.RUN_ID);

await browser.close();

if (problems.length > 0) {
  console.error("\n" + [...new Set(problems)].join("\n"));
  process.exit(1);
}
console.log("\nno console errors, no failed requests");
