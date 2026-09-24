// Parcours navigateur Comparateur ↔ Chantier (A/B/C).
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
    // ----- PARCOURS A : Enregistrer comme chantier -----
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(baseURL, { waitUntil: "networkidle" });
    await page.waitForFunction(() => !document.getElementById("example")?.disabled);

    assert.equal(await page.locator("#save-as-chantier").isHidden(), false);
    assert.equal(await page.locator("#save-as-chantier").isDisabled(), true);
    assert.equal(await page.locator("#update-chantier").isHidden(), true);

    await page.fill("#quantity", "12");
    await page.click("#add-form button");
    await page.fill("#search", "Vis");
    await page.fill("#quantity", "3");
    await page.click("#add-form button");
    assert.equal(await page.locator("#cart-body tr").count(), 2);
    assert.equal(await page.locator("#save-as-chantier").isDisabled(), false);
    assert.equal(await page.locator("#compare").isDisabled(), false);

    await page.click("#save-as-chantier");
    await page.waitForSelector("#save-as-chantier-panel:not([hidden])");
    assert.match(await page.locator("#save-as-adresse").inputValue(), /./);
    await page.fill("#save-as-nom", `TEST E2E - SAVE AS - ${RUN_TOKEN}`);
    await page.fill("#save-as-client", "Client SaveAs");
    await Promise.all([
      page.waitForURL(/\/chantiers\/\d+/),
      page.click("#save-as-submit"),
    ]);
    const createdA = Number(page.url().split("/").pop());
    tracker.track(createdA);
    results.push(`A créé id=${createdA}`);

    await page.waitForFunction(() => document.getElementById("status")?.textContent === "");
    assert.equal(await page.locator("#cart-body tr").count(), 2);
    const qtyA = await page.locator(".line-quantity").evaluateAll((nodes) =>
      nodes.map((n) => Number(n.value)).sort((a, b) => a - b),
    );
    assert.deepEqual(qtyA, [3, 12]);
    await page.reload({ waitUntil: "networkidle" });
    await page.waitForFunction(() => document.getElementById("status")?.textContent === "");
    assert.equal(await page.locator("#cart-body tr").count(), 2);
    results.push("A persistance après reload: OK");

    // ----- PARCOURS B : Mettre à jour depuis le comparateur -----
    await page.goto(`${baseURL}/chantiers/${createdA}`, { waitUntil: "networkidle" });
    await page.waitForFunction(() => !document.getElementById("compare-prices")?.disabled);
    const beforeUpdate = await api(`/api/chantiers/${createdA}`);
    assert.equal(beforeUpdate.status, 200);
    const qtyBefore = beforeUpdate.body.materiaux.map((m) => Number(m.quantite)).sort(
      (a, b) => a - b,
    );
    assert.deepEqual(qtyBefore, [3, 12]);

    await Promise.all([
      page.waitForURL(new RegExp(`[?&]chantier_id=${createdA}(?:&|$)`)),
      page.click("#compare-prices"),
    ]);
    await page.waitForFunction(() => !document.getElementById("example")?.disabled);
    assert.equal(await page.locator("#update-chantier").isHidden(), false);
    assert.equal(await page.locator("#save-as-chantier").isHidden(), true);

    await page.locator(".line-quantity").nth(0).fill("20");
    await page.fill("#search", "Rail");
    await page.fill("#quantity", "5");
    await page.click("#add-form button");
    // Pas encore sauvegardé côté API
    const mid = await api(`/api/chantiers/${createdA}`);
    assert.deepEqual(
      mid.body.materiaux.map((m) => Number(m.quantite)).sort((a, b) => a - b),
      [3, 12],
    );
    results.push("B pas d'autosave: OK");

    await page.click("#update-chantier");
    await page.waitForFunction(() =>
      document.getElementById("status")?.textContent.includes("mis à jour"),
    );
    const after = await api(`/api/chantiers/${createdA}`);
    assert.equal(after.status, 200);
    assert.equal(after.body.materiaux.length, 3);
    assert.ok(after.body.materiaux.some((m) => Number(m.quantite) === 20));
    assert.ok(after.body.materiaux.some((m) => Number(m.quantite) === 5));

    await page.click("#chantier-banner-link");
    await page.waitForURL(new RegExp(`/chantiers/${createdA}$`));
    await page.waitForFunction(() => document.getElementById("status")?.textContent === "");
    assert.equal(await page.locator("#cart-body tr").count(), 3);
    results.push("B mise à jour + lecture: OK");

    // ----- PARCOURS C : conflit 409 -----
    const conflictSource = await api("/api/chantiers", {
      method: "POST",
      body: JSON.stringify({
        nom: `TEST E2E - CONFLICT - ${RUN_TOKEN}`,
        adresse: "10 rue du Chantier, 75004 Paris",
        latitude: "48.856600",
        longitude: "2.352200",
        materiaux: [{ product_id: 1, quantite: "10", ordre: 0 }],
      }),
    });
    assert.equal(conflictSource.status, 201);
    const conflictId = conflictSource.body.id;
    tracker.track(conflictId);
    const staleToken = conflictSource.body.updated_at;

    const winner = await api(`/api/chantiers/${conflictId}`, {
      method: "PUT",
      body: JSON.stringify({
        nom: conflictSource.body.nom,
        client: null,
        adresse: conflictSource.body.adresse,
        date_prevue: null,
        notes: "gagnant navigateur",
        latitude: conflictSource.body.latitude,
        longitude: conflictSource.body.longitude,
        updated_at: conflictSource.body.updated_at,
        materiaux: [{ product_id: 1, quantite: "99", ordre: 0 }],
      }),
    });
    assert.equal(winner.status, 200);

    await page.goto(`${baseURL}/?chantier_id=${conflictId}`, { waitUntil: "networkidle" });
    await page.waitForFunction(() => !document.getElementById("example")?.disabled);
    await page.waitForSelector("#chantier-banner:not([hidden])");
    await page.waitForFunction(() => typeof window.__pmcForceLoadedUpdatedAt === "function");
    await page.evaluate((token) => window.__pmcForceLoadedUpdatedAt(token), staleToken);
    await page.click("#update-chantier");
    await page.waitForFunction(() =>
      document
        .getElementById("status")
        ?.textContent.includes("modifié depuis son chargement"),
    );
    const afterConflict = await api(`/api/chantiers/${conflictId}`);
    assert.equal(afterConflict.body.notes, "gagnant navigateur");
    assert.equal(Number(afterConflict.body.materiaux[0].quantite), 99);
    results.push("C conflit 409 UI sans écrasement: OK");

    // Origine Ma position : panneau sans fausse adresse
    await page.goto(baseURL, { waitUntil: "networkidle" });
    await page.waitForFunction(() => !document.getElementById("example")?.disabled);
    await page.click("#example");
    await page.locator('input[name="origin-type"][value="current_location"]').check();
    await page.click("#save-as-chantier");
    await page.waitForSelector("#save-as-chantier-panel:not([hidden])");
    assert.equal(await page.locator("#save-as-adresse").inputValue(), "");
    assert.equal(await page.locator("#save-as-latitude").inputValue(), "");
    assert.equal(await page.locator("#save-as-longitude").inputValue(), "");
    await page.click("#save-as-cancel");
    results.push("origine Ma position: pas de fausse adresse");

    assert.equal(errors.length, 0, errors.join(" | "));

    for (const [name, width, height] of [
      ["desktop", 1440, 1100],
      ["tablet", 768, 1024],
      ["mobile", 390, 844],
    ]) {
      const view = await browser.newPage({ viewport: { width, height } });
      await view.goto(baseURL, { waitUntil: "networkidle" });
      await view.waitForFunction(() => !document.getElementById("example")?.disabled);
      await view.click("#example");
      await view.click("#save-as-chantier");
      await view.waitForSelector("#save-as-chantier-panel:not([hidden])");
      await noOverflow(view, `${name} save-as`);
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
