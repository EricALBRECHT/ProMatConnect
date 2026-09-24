// Vérification navigateur optionnelle, indépendante de pytest et du runtime applicatif.
// Ne crée ni ne supprime aucun chantier (aucune mutation de données métier).
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const path = require("node:path");
const os = require("node:os");
const baseURL = process.env.APP_URL || "http://127.0.0.1:8000";

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [name, width, height] of [["desktop", 1440, 1100], ["mobile", 390, 844]]) {
      const page = await browser.newPage({ viewport: { width, height } });
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));
      await page.addInitScript(() => {
        window.geoCalls = 0;
        window.geoBehavior = "success";
        Object.defineProperty(navigator, "geolocation", {value: {
          getCurrentPosition(success, failure) {
            window.geoCalls++;
            if (window.geoBehavior === "denied") failure({code: 1});
            else if (window.geoBehavior === "deferred") window.deliverPosition = success;
            else success({coords: {latitude: 48.87, longitude: 2.39}});
          }
        }});
      });
      await page.goto(baseURL);
      await page.waitForFunction(() => !document.getElementById("example").disabled);
      assert.equal(await page.evaluate(() => window.geoCalls), 0, "Pas de géolocalisation au chargement");
      assert.equal(await page.locator('input[name="origin-type"]:checked').inputValue(), "site");
      // Non-régression : ajout via le formulaire (materials.js) doit activer #compare.
      assert.equal(await page.locator("#compare").isDisabled(), true);
      await page.fill("#quantity", "2");
      assert.equal(await page.locator("#quantity").getAttribute("step"), "1");
      await page.click("#add-form button");
      assert.equal(await page.locator("#cart-body tr").count(), 1);
      assert.equal(await page.locator("#compare").isDisabled(), false);
      assert.equal(await page.locator(".line-quantity").getAttribute("step"), "1");
      await page.locator(".line-quantity").evaluate((input) => {
        input.stepUp();
      });
      assert.equal(await page.locator(".line-quantity").inputValue(), "3");
      await page.locator(".line-quantity").evaluate((input) => {
        input.stepDown();
      });
      assert.equal(await page.locator(".line-quantity").inputValue(), "2");
      await page.locator(".line-quantity").fill("2.25");
      await page.click("#compare");
      await page.locator("#results-section:not([hidden])").waitFor();
      assert.equal(await page.locator(".result-card").count(), 3);
      await page.locator(".remove").click();
      assert.equal(await page.locator("#compare").isDisabled(), true);
      await page.click("#example");
      await page.click("#compare");
      await page.locator("#results-section:not([hidden])").waitFor();
      assert.equal(await page.locator(".result-card").count(), 3);
      assert.equal(await page.locator(".result-card .warning").count(), 0);
      const titles = await page.locator(".result-card h3").allTextContents();
      assert.deepEqual(titles, ["1 seul arrêt", "Prix matériaux minimum", "Meilleur compromis"]);
      await page.screenshot({path: path.join(os.tmpdir(), `promatconnect-v02-${name}.png`), fullPage: true});
      const cards = await page.locator(".result-card").evaluateAll(elements => elements.map(e => ({x: e.offsetLeft, y: e.offsetTop})));
      if (name === "mobile") assert(cards[0].y < cards[1].y && cards[1].y < cards[2].y);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.locator(".optimized summary").filter({hasText: "Itinéraire"}).click();
      assert(await page.locator(".optimized .itinerary").isVisible());
      await page.locator(".optimized summary").filter({hasText: "Comprendre"}).click();
      assert.match(await page.locator(".optimized").innerText(), /non facturé|n’est facturé/);
      await page.locator(".optimized summary").filter({hasText: "Matériaux par agence"}).click();
      assert(await page.locator(".optimized .detail-line").count() > 0);
      await page.locator(".line-quantity").first().fill("35");
      assert(await page.locator("#results-section").isHidden());
      await page.locator(".remove").last().click();
      assert.equal(await page.locator("#cart-body tr").count(), 2);
      await page.check('input[value="company"]');
      assert.equal(await page.evaluate(() => window.geoCalls), 0);
      await page.click("#compare");
      await page.locator("#results-section:not([hidden])").waitFor();
      assert.match(await page.locator("#decision-summary").innerText(), /Entreprise/);
      await page.check('input[value="other"]');
      assert(await page.locator("#results-section").isHidden());
      await page.fill("#origin-address", "Adresse inconnue de démonstration");
      await page.click("#compare");
      await page.waitForFunction(() => document.getElementById("status").textContent.includes("Adresse inconnue"));
      assert(await page.locator("#results-section").isHidden());
      await page.fill("#origin-address", "5 rue des Artisans, 94200 Ivry-sur-Seine");
      await page.click("#compare");
      await page.locator("#results-section:not([hidden])").waitFor();
      assert.match(await page.locator("#decision-summary").innerText(), /Autre adresse/);
      await page.check('input[value="current_location"]');
      assert.equal(await page.evaluate(() => window.geoCalls), 1);
      await page.click("#compare");
      await page.locator("#results-section:not([hidden])").waitFor();
      assert.match(await page.locator("#decision-summary").innerText(), /Ma position/);
      await page.evaluate(() => window.geoBehavior = "denied");
      await page.click("#locate");
      assert.match(await page.locator("#position-status").innerText(), /refusée/);
      await page.click("#compare");
      assert.match(await page.locator("#status").innerText(), /Autorisez/);
      assert(await page.locator("#results-section").isHidden());
      // Une réponse GPS tardive ne doit pas remplacer la nouvelle origine.
      await page.evaluate(() => window.geoBehavior = "deferred");
      await page.click("#locate");
      await page.check('input[value="site"]');
      await page.evaluate(() => window.deliverPosition({coords: {latitude: 49, longitude: 3}}));
      assert.equal(await page.locator('input[name="origin-type"]:checked').inputValue(), "site");
      await page.fill("#search", "mastic");
      await page.click("#add-form button");
      await page.click("#compare");
      await page.locator("#results-section:not([hidden])").waitFor();
      assert.equal(await page.locator(".result-card .warning").count(), 3);
      assert.equal(await page.locator(".result-card .estimated-cost").count(), 0);
      assert.deepEqual(errors, []);
      console.log(`${name}: panier, 3 stratégies, détail, 4 origines, refus GPS, réponse tardive, rupture : OK`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exit(1);});
