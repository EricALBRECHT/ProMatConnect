const { chromium } = require("playwright");
const { createChantierTracker } = require("./browser_cleanup.cjs");
const assert = require("node:assert/strict");

const baseURL = process.env.APP_URL || "http://127.0.0.1:8000";

async function api(pathname, options = {}) {
  const response = await fetch(`${baseURL}${pathname}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  return { status: response.status, body };
}

(async () => {
  const tracker = createChantierTracker();
  const before = await api("/api/chantiers");
  console.log(
    "AVANT",
    before.body.map((c) => [c.id, c.nom]),
  );

  const created = await api("/api/chantiers", {
    method: "POST",
    body: JSON.stringify({
      nom: `TEST E2E - DETAIL ACTIONS - ${Date.now()}`,
      adresse: "10 rue du Chantier, 75004 Paris",
      latitude: "48.856600",
      longitude: "2.352200",
      materiaux: [],
    }),
  });
  assert.equal(created.status, 201);
  const id = created.body.id;
  tracker.track(id);
  console.log("CREATED", id);

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m.text());
  });

  // PARCOURS 1
  await page.goto(`${baseURL}/chantiers/${id}`, { waitUntil: "networkidle" });
  await page.waitForFunction(() => document.getElementById("status")?.textContent === "");
  console.log("CONSOLE_ON_LOAD", consoleErrors);

  assert.equal(await page.locator("#compare-prices").isVisible(), true);
  assert.equal(await page.locator("#compare-prices").isEnabled(), true);
  assert.equal(await page.locator("#delete-chantier").isEnabled(), true);

  await page.fill("#search", "BA13");
  await page.fill("#quantity", "10");
  await page.click("#add-form button");
  await page.fill("#search", "Rail");
  await page.fill("#quantity", "4");
  await page.click("#add-form button");
  assert.equal(await page.locator("#cart-body tr").count(), 2);
  await page.click("#save-button");
  await page.waitForFunction(() =>
    document.getElementById("status")?.textContent.includes("enregistré"),
  );

  // Dirty then compare
  await page.locator(".line-quantity").first().fill("12");
  await Promise.all([
    page.waitForURL(new RegExp(`[?&]chantier_id=${id}(?:&|$)`)),
    page.click("#compare-prices"),
  ]);
  console.log("COMPARE_NAV", page.url());
  await page.waitForFunction(() => !document.getElementById("example")?.disabled);
  await page.waitForSelector("#chantier-banner:not([hidden])");
  const qtys = await page.locator(".line-quantity").evaluateAll((ns) =>
    ns.map((n) => Number(n.value)).sort((a, b) => a - b),
  );
  assert.deepEqual(qtys, [4, 12]);
  await page.click("#compare");
  await page.locator("#results-section:not([hidden])").waitFor();
  assert.equal(await page.locator(".result-card").count(), 3);
  console.log("STRATEGIES_OK");

  // Clean path (non dirty)
  await page.goto(`${baseURL}/chantiers/${id}`, { waitUntil: "networkidle" });
  await page.waitForFunction(() => document.getElementById("status")?.textContent === "");
  await Promise.all([
    page.waitForURL(new RegExp(`[?&]chantier_id=${id}(?:&|$)`)),
    page.click("#compare-prices"),
  ]);
  console.log("COMPARE_CLEAN_NAV_OK");

  // PARCOURS 2 — delete cancel then confirm
  await page.goto(`${baseURL}/chantiers/${id}`, { waitUntil: "networkidle" });
  await page.waitForFunction(() => document.getElementById("status")?.textContent === "");
  await page.click("#delete-chantier");
  await page.waitForSelector("#delete-chantier-panel:not([hidden])");
  await page.click("#delete-chantier-cancel");
  assert.equal(await page.locator("#delete-chantier-panel").isHidden(), true);
  assert.equal((await api(`/api/chantiers/${id}`)).status, 200);
  console.log("DELETE_CANCEL_OK");

  await page.click("#delete-chantier");
  await page.click("#delete-chantier-confirm");
  await page.waitForURL(/\/chantiers(\?|$)/);
  assert.equal((await api(`/api/chantiers/${id}`)).status, 404);
  console.log("DELETE_CONFIRM_OK", page.url());

  console.log("PAGE_ERRORS", pageErrors);
  assert.equal(pageErrors.length, 0, pageErrors.join(" | "));

  for (const [name, width, height] of [
    ["desktop", 1440, 1100],
    ["tablet", 768, 1024],
    ["mobile", 390, 844],
  ]) {
    const view = await browser.newPage({ viewport: { width, height } });
    await view.goto(baseURL, { waitUntil: "networkidle" });
    await view.waitForFunction(() => !document.getElementById("example")?.disabled);
    const sizes = await view.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      inner: window.innerWidth,
    }));
    assert.ok(sizes.scroll <= sizes.inner + 1, `${name} overflow`);
    console.log(`${name} ok`);
    await view.close();
  }

  await page.close();
  await browser.close();

  const deleted = await tracker.cleanup(async (path, options) => {
    const response = await fetch(`${baseURL}${path}`, options);
    return {
      ok: response.ok,
      status: response.status,
      async json() {
        return response.json();
      },
    };
  });
  console.log("cleanup", deleted);

  const after = await api("/api/chantiers");
  console.log(
    "APRES",
    after.body.map((c) => [c.id, c.nom]),
  );
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
