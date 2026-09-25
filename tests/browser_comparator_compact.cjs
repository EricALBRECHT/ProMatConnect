// Validation navigateur mode compact chantier + mode ponctuel.
// Crée un chantier de test, le nettoie en fin de script.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");

const baseURL = process.env.APP_URL || "http://127.0.0.1:8000";
const createdIds = [];

async function api(path, options = {}) {
  const response = await fetch(`${baseURL}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  let json = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    json = null;
  }
  return { response, json, text };
}

async function cleanup() {
  for (const id of createdIds.splice(0)) {
    try {
      const get = await api(`/api/chantiers/${id}`);
      if (get.response.status !== 200) continue;
      const updatedAt = encodeURIComponent(get.json.updated_at);
      await fetch(`${baseURL}/api/chantiers/${id}?updated_at=${updatedAt}`, {
        method: "DELETE",
      });
    } catch {
      /* ignore */
    }
  }
}

function noHScroll(page) {
  return page.evaluate(
    () =>
      document.documentElement.scrollWidth <=
      document.documentElement.clientWidth + 1,
  );
}

(async () => {
  const created = await api("/api/chantiers", {
    method: "POST",
    body: JSON.stringify({
      nom: "dupont",
      adresse: "10 rue du Chantier, 75004 Paris",
      materiaux: [{ product_id: 1, quantite: "1", ordre: 0 }],
    }),
  });
  assert.equal(created.response.status, 201, created.text);
  createdIds.push(created.json.id);
  const chantierId = created.json.id;

  const browser = await chromium.launch({ headless: true });
  const report = {
    chantier: {},
    ponctuel: {},
    responsive: {},
  };

  try {
    // ——— Parcours chantier compact ———
    {
      const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
      const errors = [];
      page.on("pageerror", (e) => errors.push(e.message));
      await page.goto(`${baseURL}/chantiers/${chantierId}`);
      await page.waitForSelector("#compare-prices");
      await page.click("#compare-prices");
      await page.waitForURL(`**/?chantier_id=${chantierId}`);
      await page.waitForSelector("#chantier-banner:not([hidden])");
      await page.waitForFunction(() => {
        const name = document.getElementById("chantier-banner-name");
        return name && name.textContent.trim().length > 0;
      });

      assert.match(await page.locator("#chantier-banner-name").innerText(), /dupont/i);
      assert.match(
        await page.locator("#chantier-context-adresse").innerText(),
        /10 rue du Chantier/,
      );
      assert.equal(await page.locator("#comparator-intro").isVisible(), false);
      assert.equal(await page.locator("#origin-summary").isVisible(), true);
      assert.equal(await page.locator("#origin-detail").isVisible(), false);
      assert.equal(await page.locator("#needs-summary").isVisible(), true);
      assert.equal(await page.locator("#needs-detail").isVisible(), false);
      assert.equal(await page.locator("#compare").isVisible(), true);
      assert.match(await page.locator("#compare").innerText(), /Comparer les prix/);
      assert.equal(
        await page.locator("#origin-expand").getAttribute("aria-expanded"),
        "false",
      );
      assert.equal(
        await page.locator("#needs-expand").getAttribute("aria-expanded"),
        "false",
      );

      await page.click("#origin-expand");
      assert.equal(await page.locator("#origin-detail").isVisible(), true);
      assert.equal(await page.locator("#origin-summary").isVisible(), false);
      assert.equal(
        await page.locator("#origin-expand").getAttribute("aria-expanded"),
        "true",
      );
      await page.click("#origin-collapse");
      assert.equal(await page.locator("#origin-detail").isVisible(), false);
      assert.equal(await page.locator("#origin-summary").isVisible(), true);

      await page.click("#needs-expand");
      assert.equal(await page.locator("#needs-detail").isVisible(), true);
      await page.click("#needs-collapse");
      assert.equal(await page.locator("#needs-detail").isVisible(), false);

      // Comparer avec sections repliées
      await page.click("#compare");
      await page.waitForSelector("#results-section:not([hidden])");
      await page.waitForSelector(".result-card");
      const cards = await page.locator(".result-card").count();
      assert.equal(cards, 3);
      assert.equal(await page.locator("#origin-detail").isVisible(), false);
      assert.equal(await page.locator("#needs-detail").isVisible(), false);

      const choose = page.locator(".strategy-choose .button").first();
      await choose.click();
      await page.waitForSelector("#choice-confirm-panel:not([hidden])");
      await page.click("#choice-confirm-submit");
      await page.waitForURL(`**/chantiers/${chantierId}**`);
      assert.match(page.url(), /approvisionnement=retenu/);
      await page.waitForSelector("#appro-content:not([hidden])");
      report.chantier.compareChooseReturn = "OK";

      // Second passage : modifier liste
      await page.click("#compare-prices");
      await page.waitForURL(`**/?chantier_id=${chantierId}`);
      await page.waitForSelector("#chantier-banner:not([hidden])");
      await page.click("#needs-expand");
      await page.waitForSelector("#needs-detail:not([hidden])");
      await page.fill(".line-quantity", "3");
      await page.click("#update-chantier");
      await page.waitForFunction(() => {
        const s = document.getElementById("status");
        return s && /Liste enregistrée/i.test(s.textContent || "");
      });
      const after = await api(`/api/chantiers/${chantierId}`);
      assert.equal(Number(after.json.materiaux[0].quantite), 3);
      await page.click("#compare");
      await page.waitForSelector("#results-section:not([hidden]) .result-card");
      await page.locator(".strategy-choose .button").first().click();
      await page.waitForSelector("#choice-confirm-panel:not([hidden])");
      await page.click("#choice-confirm-submit");
      await page.waitForURL(`**/chantiers/${chantierId}**`);
      report.chantier.modifySaveCompare = "OK";
      assert.equal(errors.length, 0, errors.join(" | "));
      await page.close();
    }

    // ——— Parcours ponctuel ———
    {
      const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
      const errors = [];
      page.on("pageerror", (e) => errors.push(e.message));
      await page.goto(baseURL);
      await page.waitForFunction(() => !document.getElementById("example").disabled);
      assert.equal(await page.locator("#comparator-intro").isVisible(), true);
      assert.match(await page.locator("h1").innerText(), /Préparez votre liste/);
      assert.equal(await page.locator("#chantier-banner").isVisible(), false);
      assert.equal(await page.locator("#origin-detail").isVisible(), true);
      assert.equal(await page.locator("#needs-detail").isVisible(), true);
      assert.equal(await page.locator("#origin-summary").isVisible(), false);
      assert.equal(await page.locator("#needs-summary").isVisible(), false);
      assert.match(await page.locator("#compare").innerText(), /Comparer mon panier/);
      await page.click("#example");
      await page.click("#compare");
      await page.waitForSelector("#results-section:not([hidden]) .result-card");
      assert.equal(await page.locator(".result-card").count(), 3);
      report.ponctuel = "OK";
      assert.equal(errors.length, 0, errors.join(" | "));
      await page.close();
    }

    // ——— Responsive ———
    for (const [label, width, height] of [
      ["1440", 1440, 1100],
      ["768", 768, 1024],
      ["390", 390, 844],
    ]) {
      const page = await browser.newPage({ viewport: { width, height } });
      await page.goto(`${baseURL}/?chantier_id=${chantierId}`);
      await page.waitForSelector("#chantier-banner:not([hidden])");
      await page.waitForFunction(() => {
        const el = document.getElementById("chantier-banner-name");
        return el && el.textContent.trim().length > 0;
      });
      const visible = {
        context: await page.locator("#chantier-banner").isVisible(),
        address: await page.locator("#chantier-context-adresse").isVisible(),
        needs: await page.locator("#needs-summary").isVisible(),
        compare: await page.locator("#compare").isVisible(),
        originDetail: await page.locator("#origin-detail").isVisible(),
        needsDetail: await page.locator("#needs-detail").isVisible(),
        noHScroll: await noHScroll(page),
      };
      assert.equal(visible.context, true);
      assert.equal(visible.address, true);
      assert.equal(visible.needs, true);
      assert.equal(visible.compare, true);
      assert.equal(visible.originDetail, false);
      assert.equal(visible.needsDetail, false);
      assert.equal(visible.noHScroll, true);
      report.responsive[label] = "OK";
      await page.close();
    }

    console.log(JSON.stringify(report, null, 2));
    console.log("BROWSER_VALIDATION_OK");
  } finally {
    await browser.close();
    await cleanup();
  }
})().catch(async (error) => {
  console.error("BROWSER_VALIDATION_FAIL", error);
  await cleanup();
  process.exit(1);
});
