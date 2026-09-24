// Parcours navigateur : approvisionnement retenu + suppression chantier.
// Cleanup strict : uniquement les IDs créés pendant cette exécution.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const { createChantierTracker } = require("./browser_cleanup.cjs");

const baseURL = process.env.APP_URL || "http://127.0.0.1:8000";
const RUN_TOKEN = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

async function api(pathname, options = {}) {
  const response = await fetch(`${baseURL}${pathname}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  return { status: response.status, body };
}

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

    // 1-2 Créer chantier + matériaux
    await page.goto(`${baseURL}/chantiers/nouveau`, { waitUntil: "networkidle" });
    await page.fill("#nom", `TEST E2E - APPRO - ${RUN_TOKEN}`);
    await page.fill("#adresse", "10 rue du Chantier, 75004 Paris");
    await page.evaluate(() => {
      document.getElementById("latitude").value = "48.856600";
      document.getElementById("longitude").value = "2.352200";
    });
    await Promise.all([
      page.waitForURL(/\/chantiers\/\d+/),
      page.click("#create-button"),
    ]);
    const chantierId = Number(page.url().split("/").pop());
    tracker.track(chantierId);
    results.push(`créé id=${chantierId}`);

    await page.waitForFunction(() => document.getElementById("status")?.textContent === "");
    await page.fill("#search", "BA13");
    await page.fill("#quantity", "10");
    await page.click("#add-form button");
    await page.fill("#search", "Rail");
    await page.fill("#quantity", "4");
    await page.click("#add-form button");
    await page.click("#save-button");
    await page.waitForFunction(() =>
      document.getElementById("status")?.textContent.includes("enregistré"),
    );
    assert.equal(await page.locator("#appro-empty").isVisible(), true);

    // 3-5 Comparer et choisir
    await Promise.all([
      page.waitForURL(new RegExp(`[?&]chantier_id=${chantierId}(?:&|$)`)),
      page.click("#compare-prices"),
    ]);
    await page.waitForFunction(() => !document.getElementById("example")?.disabled);
    await page.click("#compare");
    await page.locator("#results-section:not([hidden])").waitFor();
    assert.equal(await page.locator(".strategy-choose").count(), 3);
    // Sans chantier_id, pas de bouton — vérifié plus bas.

    await page.locator(".strategy-choose .button").first().click();
    await page.waitForSelector("#choice-confirm-panel:not([hidden])");
    await page.click("#choice-confirm-submit");
    await page.waitForFunction(() =>
      document.getElementById("status")?.textContent.includes("retenu"),
    );
    results.push("choix stratégie: OK");

    // 6-7 Retour détail + vérifier appro
    await page.click("#chantier-banner-link");
    await page.waitForURL(new RegExp(`/chantiers/${chantierId}$`));
    await page.waitForFunction(() => document.getElementById("appro-content") && !document.getElementById("appro-content").hidden);
    assert.equal(await page.locator("#appro-empty").isHidden(), true);
    assert.match(await page.locator("#appro-content").innerText(), /Stratégie/);
    results.push("affichage appro: OK");

    // 8-9 Modifier quantité → obsolescence
    await page.locator(".line-quantity").first().fill("15");
    await page.click("#save-button");
    await page.waitForFunction(() =>
      document.getElementById("status")?.textContent.includes("enregistré"),
    );
    await page.waitForSelector("#appro-obsolete:not([hidden])");
    assert.match(
      await page.locator("#appro-obsolete").innerText(),
      /besoins du chantier ont changé/,
    );
    results.push("obsolescence: OK");

    // 10-12 Nouvelle comparaison + remplacement
    await Promise.all([
      page.waitForURL(new RegExp(`[?&]chantier_id=${chantierId}(?:&|$)`)),
      page.click("#appro-recompare"),
    ]);
    await page.waitForFunction(() => !document.getElementById("example")?.disabled);
    await page.click("#compare");
    await page.locator("#results-section:not([hidden])").waitFor();
    await page.locator(".strategy-choose .button").nth(1).click();
    await page.waitForSelector("#choice-confirm-panel:not([hidden])");
    assert.match(
      await page.locator("#choice-confirm-body").innerText(),
      /déjà un approvisionnement/,
    );
    await page.click("#choice-confirm-submit");
    await page.waitForFunction(() =>
      document.getElementById("status")?.textContent.includes("retenu"),
    );
    await page.click("#chantier-banner-link");
    await page.waitForURL(new RegExp(`/chantiers/${chantierId}$`));
    await page.waitForFunction(() => !document.getElementById("appro-content")?.hidden);
    assert.equal(await page.locator("#appro-obsolete").isHidden(), true);
    results.push("remplacement: OK");

    // 13-14 Retirer appro, matériaux restent
    page.once("dialog", (dialog) => dialog.accept());
    await page.click("#appro-clear");
    await page.waitForFunction(() =>
      document.getElementById("status")?.textContent.includes("retiré"),
    );
    assert.equal(await page.locator("#appro-empty").isVisible(), true);
    assert.equal(await page.locator("#cart-body tr").count(), 2);
    const kept = await api(`/api/chantiers/${chantierId}`);
    assert.equal(kept.body.materiaux.length, 2);
    results.push("retrait appro conserve besoins: OK");

    // Sans chantier_id : pas de bouton choisir
    await page.goto(baseURL, { waitUntil: "networkidle" });
    await page.waitForFunction(() => !document.getElementById("example")?.disabled);
    await page.click("#example");
    await page.click("#compare");
    await page.locator("#results-section:not([hidden])").waitFor();
    assert.equal(await page.locator(".strategy-choose").count(), 0);
    results.push("comparaison ponctuelle sans choix: OK");

    // 15-16 Suppression UI
    await page.goto(`${baseURL}/chantiers/${chantierId}`, { waitUntil: "networkidle" });
    await page.waitForFunction(() => document.getElementById("status")?.textContent === "");
    await page.click("#delete-chantier");
    await page.waitForSelector("#delete-chantier-panel:not([hidden])");
    await page.click("#delete-chantier-cancel");
    assert.equal(await page.locator("#delete-chantier-panel").isHidden(), true);
    await page.click("#delete-chantier");
    await page.click("#delete-chantier-confirm");
    await page.waitForURL(/\/chantiers(\?|$)/);
    assert.match(await page.locator("#status").innerText(), /supprimé/i);
    const gone = await api(`/api/chantiers/${chantierId}`);
    assert.equal(gone.status, 404);
    tracker.ids().forEach((id) => {
      // déjà supprimé via UI — le cleanup tolère 404
    });
    results.push("suppression UI: OK");

    assert.equal(errors.length, 0, errors.join(" | "));

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
    await page.close();
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
