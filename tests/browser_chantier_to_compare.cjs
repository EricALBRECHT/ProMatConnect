// Parcours navigateur chantier → comparateur.
// Cleanup strict : uniquement les IDs créés pendant cette exécution.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const { createChantierTracker } = require("./browser_cleanup.cjs");

const baseURL = process.env.APP_URL || "http://127.0.0.1:8000";
const RUN_TOKEN = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
const TEST_NAME = `TEST E2E - TRANSFERT COMPARE - ${RUN_TOKEN}`;

async function noOverflow(page, label) {
  const sizes = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    inner: window.innerWidth,
  }));
  if (sizes.scroll > sizes.inner + 1) {
    throw new Error(`${label}: overflow ${sizes.scroll}>${sizes.inner}`);
  }
}

(async () => {
  const tracker = createChantierTracker();
  const browser = await chromium.launch({ headless: true });
  const results = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));

    await page.goto(`${baseURL}/chantiers/nouveau`, { waitUntil: "networkidle" });
    await page.fill("#nom", TEST_NAME);
    await page.fill("#client", "Client Parcours");
    await page.fill("#adresse", "10 rue du Chantier, 75004 Paris");
    await page.evaluate(() => {
      document.getElementById("latitude").value = "48.856600";
      document.getElementById("longitude").value = "2.352200";
    });
    await Promise.all([
      page.waitForURL(/\/chantiers\/\d+/),
      page.click("#create-button"),
    ]);
    const createdId = Number(page.url().split("/").pop());
    tracker.track(createdId);
    results.push(`créé id=${createdId}`);

    await page.waitForFunction(() => document.getElementById("status")?.textContent === "");
    await page.fill("#search", "BA13");
    await page.fill("#quantity", "12");
    await page.click("#add-form button");
    await page.fill("#search", "Vis");
    await page.fill("#quantity", "3");
    await page.click("#add-form button");
    assert.equal(await page.locator("#cart-body tr").count(), 2);
    await page.locator(".line-quantity").nth(0).fill("12");
    await page.locator(".line-quantity").nth(1).fill("3");
    await page.click("#save-button");
    await page.waitForFunction(() =>
      document.getElementById("status")?.textContent.includes("enregistré"),
    );

    assert.equal(await page.locator("#compare-prices").isDisabled(), false);
    await Promise.all([
      page.waitForURL(new RegExp(`[?&]chantier_id=${createdId}(?:&|$)`)),
      page.click("#compare-prices"),
    ]);
    await page.waitForFunction(() => !document.getElementById("example")?.disabled);
    await page.waitForFunction(
      () =>
        document.getElementById("chantier-banner") &&
        !document.getElementById("chantier-banner").hidden,
    );
    assert.match(await page.locator("#chantier-banner").innerText(), /TEST E2E - TRANSFERT COMPARE/);
    assert.equal(await page.locator('input[name="origin-type"]:checked').inputValue(), "site");
    assert.equal(await page.locator("#cart-body tr").count(), 2);
    const quantities = await page.locator(".line-quantity").evaluateAll((nodes) =>
      nodes.map((n) => Number(n.value)),
    );
    assert.deepEqual(quantities.sort((a, b) => a - b), [3, 12]);
    assert.equal(await page.locator("#results-section").isHidden(), true);
    await page.click("#compare");
    await page.locator("#results-section:not([hidden])").waitFor();
    assert.equal(await page.locator(".result-card").count(), 3);
    results.push("parcours chantier → comparateur + 3 stratégies: OK");

    await page.click("#chantier-banner-link");
    await page.waitForURL(new RegExp(`/chantiers/${createdId}$`));
    assert.equal(await page.locator("#nom").inputValue(), TEST_NAME);
    results.push("retour au chantier: OK");

    await page.goto(`${baseURL}/?chantier_id=999999`, { waitUntil: "networkidle" });
    await page.waitForFunction(() =>
      document.getElementById("status")?.textContent.includes("introuvable"),
    );
    await page.goto(`${baseURL}/?chantier_id=abc`, { waitUntil: "networkidle" });
    await page.waitForFunction(() =>
      document.getElementById("status")?.textContent.includes("introuvable"),
    );
    results.push("ids invalides: OK");

    assert.equal(errors.length, 0, errors.join(" | "));
    await page.close();

    for (const [name, width, height] of [
      ["desktop", 1440, 1100],
      ["tablet", 768, 1024],
      ["mobile", 390, 844],
    ]) {
      const view = await browser.newPage({ viewport: { width, height } });
      await view.goto(baseURL, { waitUntil: "networkidle" });
      await view.waitForFunction(() => !document.getElementById("example")?.disabled);
      await noOverflow(view, `${name} /`);
      await view.goto(`${baseURL}/chantiers`, { waitUntil: "networkidle" });
      await view.waitForFunction(
        () => !document.getElementById("status")?.textContent.includes("Chargement"),
      );
      await noOverflow(view, `${name} /chantiers`);
      results.push(`${name} ${width}px: pas de scroll horizontal`);
      await view.close();
    }
  } finally {
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
    results.push(`cleanup ids=${JSON.stringify(deleted)}`);
  }
  console.log(results.join("\n"));
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
