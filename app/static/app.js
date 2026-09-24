"use strict";

const {
  $,
  node,
  fetchProducts,
  fillProductSelect,
  bindMaterialLines,
  apiErrorMessage,
  reportQuantityFields,
} = window.ProMatMaterials;

const money = (value) =>
  new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR" }).format(
    value,
  );
const number = (value) =>
  new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 3 }).format(value);

let products = [];
let cart = [];
let revision = 0;
/** Coordonnées du chantier chargé ; null si adresse seule ou édition manuelle. */
let siteCoordinates = null;
let siteAddressNeedsConfirmation = false;

/** Chantier chargé via ?chantier_id= ; null hors de ce mode. */
let loadedChantier = null;
const UPDATE_CONFLICT_MESSAGE =
  "Ce chantier a été modifié depuis son chargement. Rechargez-le avant de poursuivre.";
const EMPTY_UPDATE_CONFIRM =
  "Le chantier ne contiendra plus aucun matériau. Continuer ?";
const STALE_COMPARISON_MESSAGE =
  "Le chantier a été modifié depuis cette comparaison. Relancez la comparaison avant de choisir une solution.";
/** Empreinte du panier au moment de la dernière comparaison réussie. */
let comparisonFingerprint = null;
let pendingChoice = null;

function status(message = "", error = false) {
  $("status").textContent = message;
  $("status").className = error ? "status error" : "status";
}

function invalidate() {
  revision++;
  $("results-section").hidden = true;
  comparisonFingerprint = null;
  pendingChoice = null;
  hideChoiceConfirm();
  status();
}

function materialsFingerprint(lines) {
  return [...lines]
    .map((line) => ({
      id: Number(line.product_id),
      quantity: Number(line.quantity ?? line.quantite),
    }))
    .sort((a, b) => a.id - b.id)
    .map((line) => `${line.id}:${line.quantity.toFixed(3)}`)
    .join("|");
}

function hideChoiceConfirm() {
  const panel = $("choice-confirm-panel");
  if (panel) panel.hidden = true;
  pendingChoice = null;
}

function syncCompareButton() {
  // Historique : le panier non vide active le bouton ; l'origine se valide au clic.
  $("compare").disabled = cart.length === 0;
}

function syncChantierActions() {
  const saveBtn = $("save-as-chantier");
  const updateBtn = $("update-chantier");
  if (!saveBtn || !updateBtn) return;
  const fromChantier = loadedChantier !== null;
  saveBtn.hidden = fromChantier;
  updateBtn.hidden = !fromChantier;
  saveBtn.disabled = fromChantier || cart.length === 0;
  updateBtn.disabled = !fromChantier;
  if (fromChantier) hideSaveAsPanel();
}

function optionalText(value) {
  const trimmed = String(value || "").trim();
  return trimmed || null;
}

function cartMateriauxPayload() {
  return cart.map((line, index) => ({
    product_id: line.product_id,
    quantite: line.quantity,
    ordre: index,
  }));
}

function companyMeta() {
  const el = $("comparator-meta");
  if (!el) return { address: "", latitude: null, longitude: null };
  const lat = (el.dataset.companyLatitude || "").trim();
  const lng = (el.dataset.companyLongitude || "").trim();
  return {
    address: (el.dataset.companyAddress || "").trim(),
    latitude: lat === "" ? null : lat,
    longitude: lng === "" ? null : lng,
  };
}

/**
 * Adresse / coords pour « Enregistrer comme chantier ».
 * Ma position → jamais d'adresse inventée ni de coords GPS comme faux chantier.
 */
function resolveSaveAsDefaults() {
  const type = originType();
  if (type === "current_location") {
    return { adresse: "", latitude: null, longitude: null };
  }
  if (type === "company") {
    const meta = companyMeta();
    return {
      adresse: meta.address,
      latitude: meta.latitude,
      longitude: meta.longitude,
    };
  }
  const adresse = $("origin-address").value.trim();
  if (type === "site" && siteCoordinates) {
    return {
      adresse,
      latitude: String(siteCoordinates.latitude),
      longitude: String(siteCoordinates.longitude),
    };
  }
  return { adresse, latitude: null, longitude: null };
}

function hideSaveAsPanel() {
  const panel = $("save-as-chantier-panel");
  if (panel) panel.hidden = true;
}

function openSaveAsPanel() {
  if (cart.length === 0 || loadedChantier) return;
  const defaults = resolveSaveAsDefaults();
  $("save-as-nom").value = "";
  $("save-as-client").value = "";
  $("save-as-adresse").value = defaults.adresse;
  $("save-as-date").value = "";
  $("save-as-notes").value = "";
  $("save-as-latitude").value = defaults.latitude ?? "";
  $("save-as-longitude").value = defaults.longitude ?? "";
  $("save-as-chantier-panel").hidden = false;
  $("save-as-nom").focus();
  $("save-as-chantier-panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

function rememberLoadedChantier(chantier) {
  loadedChantier = {
    id: chantier.id,
    updated_at: chantier.updated_at,
    nom: chantier.nom,
    client: chantier.client,
    adresse: chantier.adresse,
    date_prevue: chantier.date_prevue,
    notes: chantier.notes,
    latitude: chantier.latitude,
    longitude: chantier.longitude,
  };
}

/** Hook de contrôle navigateur : forcer un updated_at périmé. */
window.__pmcForceLoadedUpdatedAt = (token) => {
  if (loadedChantier) loadedChantier.updated_at = token;
};

function clearLoadedChantier() {
  loadedChantier = null;
  hideSaveAsPanel();
}

function normalizeAddress(address) {
  return String(address || "")
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/gi, " ")
    .trim()
    .replace(/\s+/g, " ");
}

function demoAddresses() {
  return [...document.querySelectorAll("#demo-addresses option")].map(
    (option) => option.value,
  );
}

function isKnownDemoAddress(address) {
  const needle = normalizeAddress(address);
  return demoAddresses().some((item) => normalizeAddress(item) === needle);
}

function mergeProductsFromChantier(materiaux) {
  const byId = new Map(products.map((product) => [product.id, product]));
  materiaux.forEach((line) => {
    if (byId.has(line.product_id)) return;
    // Produit hors première page / filtre courant : conserver depuis l'API chantier.
    byId.set(line.product_id, {
      id: line.product_id,
      code: line.product_code,
      name: line.product_name,
      category: line.product_category,
      reference_unit: line.unite,
      description: null,
    });
  });
  products = [...byId.values()];
}

function showChantierBanner(chantier) {
  const banner = $("chantier-banner");
  if (!banner) return;
  $("chantier-banner-name").textContent = chantier.nom;
  $("chantier-banner-link").href = `/chantiers/${chantier.id}`;
  banner.hidden = false;
}

function hideChantierBanner() {
  const banner = $("chantier-banner");
  if (banner) banner.hidden = true;
  clearLoadedChantier();
  syncChantierActions();
}

function applySiteOriginFromChantier(chantier) {
  const siteRadio = document.querySelector('input[name="origin-type"][value="site"]');
  if (siteRadio && !siteRadio.checked) {
    siteRadio.checked = true;
    siteRadio.dispatchEvent(new Event("change", { bubbles: true }));
  }
  $("origin-address").value = chantier.adresse || "";
  savedAddresses.site = $("origin-address").value;
  previousOriginType = "site";
  const hasCoords =
    chantier.latitude !== null &&
    chantier.latitude !== undefined &&
    chantier.longitude !== null &&
    chantier.longitude !== undefined;
  if (hasCoords) {
    siteCoordinates = {
      latitude: Number(chantier.latitude),
      longitude: Number(chantier.longitude),
    };
    siteAddressNeedsConfirmation = false;
    return;
  }
  siteCoordinates = null;
  siteAddressNeedsConfirmation = !isKnownDemoAddress(chantier.adresse || "");
}

async function loadChantierFromQuery() {
  const raw = new URLSearchParams(window.location.search).get("chantier_id");
  if (raw === null) {
    clearLoadedChantier();
    syncChantierActions();
    return;
  }
  if (!/^\d+$/.test(raw) || Number(raw) < 1) {
    hideChantierBanner();
    status("Le chantier demandé est introuvable.", true);
    return;
  }
  const chantierId = Number(raw);
  status("Chargement du chantier…");
  try {
    const response = await fetch(`/api/chantiers/${chantierId}`);
    if (response.status === 404) {
      hideChantierBanner();
      cart = [];
      siteCoordinates = null;
      siteAddressNeedsConfirmation = false;
      invalidate();
      renderCart();
      status("Le chantier demandé est introuvable.", true);
      return;
    }
    if (!response.ok) throw new Error("Impossible de charger ce chantier.");
    const chantier = await response.json();
    mergeProductsFromChantier(chantier.materiaux || []);
    cart = (chantier.materiaux || []).map((line) => ({
      product_id: line.product_id,
      quantity: String(line.quantite),
    }));
    applySiteOriginFromChantier(chantier);
    rememberLoadedChantier(chantier);
    showChantierBanner(chantier);
    invalidate();
    renderProducts();
    renderCart();
    if (siteAddressNeedsConfirmation) {
      status(
        "Adresse du chantier inconnue du géocodeur de démonstration. " +
          "Choisissez une adresse de test proposée ou Ma position avant de comparer.",
        true,
      );
    } else {
      status(`Panier chargé depuis « ${chantier.nom} ».`);
    }
  } catch (error) {
    hideChantierBanner();
    status(error.message || "Connexion impossible. Réessayez.", true);
  }
}

function getCart() {
  return cart;
}

function setCart(lines) {
  cart = lines;
}

const materialList = bindMaterialLines({
  getProducts: () => products,
  getLines: getCart,
  setLines: setCart,
  onChange: () => {
    invalidate();
    syncCompareButton();
    syncChantierActions();
  },
  status,
});

const renderProducts = materialList.renderProducts;
const renderCart = () => {
  materialList.renderLines();
  syncCompareButton();
  syncChantierActions();
};

$("example").addEventListener("click", () => {
  cart = ["PMC0001", "PMC0002", "PMC0003"].map((code, i) => ({
    product_id: products.find((p) => p.code === code).id,
    quantity: ["30", "10", "20"][i],
  }));
  invalidate();
  renderCart();
});

function renderStrategy(option) {
  const card = node(
    "article",
    undefined,
    `result-card ${option.key === "best_compromise" ? "optimized" : ""}`,
  );
  const labels = {
    single_stop: "Tout retirer au même endroit",
    minimum_materials: "Le prix d’achat le plus bas",
    best_compromise: "Matériaux + temps + déplacements",
  };
  card.append(
    node("div", labels[option.key], "card-label"),
    node("h3", option.title),
  );
  card.append(
    node(
      "span",
      option.valid ? "Panier complet disponible" : "Solution indisponible",
      `badge ${option.valid ? "" : "warning"}`,
    ),
  );
  if (!option.valid) {
    card.append(node("p", option.explanation, "unavailable-description"));
    const details = node("details");
    details.append(node("summary", "Disponibilité des produits"));
    option.unavailable.forEach((line) => {
      const item = node("div", undefined, "detail-line");
      item.append(node("strong", line.product_name), node("p", line.reason));
      details.append(item);
    });
    card.append(details);
    return card;
  }
  const price = node("p", money(option.material_total), "price");
  price.append(node("small", " HT"));
  card.append(price, node("p", "Prix des matériaux", "price-label"));
  const metrics = node("dl", undefined, "metrics");
  [
    ["Trajet aller-retour", `${number(option.travel_minutes)} min`],
    ["Distance totale", `${number(option.total_distance_km)} km`],
    [
      "Arrêts / fournisseurs",
      `${option.stops.length} / ${option.supplier_count}`,
    ],
    ["Préparation max.", `${option.max_preparation_minutes} min`],
  ].forEach(([label, value]) => {
    const group = node("div");
    group.append(node("dt", label), node("dd", value));
    metrics.append(group);
  });
  card.append(metrics);
  const agencies = node(
    "p",
    option.stops.map((s) => s.name).join(" → "),
    "selected-agencies",
  );
  card.append(agencies);
  const estimated = node("div", undefined, "estimated-cost");
  estimated.append(
    node("span", "Coût estimé d’approvisionnement"),
    node("strong", money(option.estimated_procurement_cost)),
    node("small", "Indicateur de comparaison · non facturé"),
  );
  card.append(estimated);
  const route = node("details");
  route.append(node("summary", "Itinéraire aller-retour"));
  const itinerary = node("ol", undefined, "itinerary");
  itinerary.append(node("li", option.route.points[0].label));
  option.route.legs.forEach((leg) => {
    const item = node("li");
    item.append(
      node(
        "small",
        `↓ ${number(leg.duration_minutes)} min — ${number(leg.distance_km)} km`,
      ),
      node("strong", leg.end.label),
    );
    itinerary.append(item);
  });
  route.append(
    itinerary,
    node(
      "p",
      "Trajet routier simulé. Ordre optimisé pour la durée, puis la distance.",
      "footnote",
    ),
  );
  card.append(route);
  const calculation = node("details");
  calculation.append(node("summary", "Comprendre le calcul"));
  const breakdown = option.cost_breakdown;
  const parameters = lastComparison.cost_parameters;
  [
    option.explanation,
    `Matériaux : ${money(option.material_total)} HT`,
    `Distance : ${number(option.total_distance_km)} km × ${money(parameters.cost_per_km)}/km = ${money(breakdown.distance_cost)}`,
    `Temps : (${number(option.travel_minutes)} min de trajet + ${option.max_preparation_minutes} min de préparation) × ${money(parameters.time_value_per_hour)}/h ÷ 60 = ${money(breakdown.time_cost)}`,
    `Arrêts supplémentaires : ${Math.max(option.stops.length - 1, 0)} × ${money(parameters.extra_stop_cost)} = ${money(breakdown.extra_stops_cost)}`,
    `Temps total estimé : ${number(option.total_minutes)} min. Préparations parallèles, attendues avant le départ.`,
    `Coût estimé : ${money(option.estimated_procurement_cost)}. Aucun montant de déplacement ou de temps n’est facturé par l’application.`,
  ].forEach((text) => calculation.append(node("p", text)));
  card.append(calculation);
  const productsBlock = node("details");
  productsBlock.append(node("summary", "Matériaux par agence"));
  option.stops.forEach((stop) => {
    productsBlock.append(
      node("h4", stop.name),
      node("p", `${stop.address}, ${stop.postal_code} ${stop.city}`),
    );
    option.lines
      .filter((line) => line.agency_id === stop.id)
      .forEach((line) => {
        const detail = node("div", undefined, "detail-line");
        detail.append(
          node("strong", line.product_name),
          node(
            "p",
            `${number(line.requested_quantity)} ${line.reference_unit} demandés · ${money(line.line_total)} HT`,
          ),
          node(
            "p",
            `${line.packs} × ${line.supplier_unit} à ${money(line.pack_price)} HT · ${number(line.purchased_quantity)} ${line.reference_unit} achetés`,
          ),
          node("p", `${line.supplier} · réf. ${line.supplier_reference}`),
          node(
            "p",
            `Stock : ${number(line.available_quantity)} ${line.reference_unit} · Préparation : ${line.preparation_minutes} min`,
          ),
          node(
            "small",
            `Offre fictive du ${new Date(line.updated_at).toLocaleDateString("fr-FR")}`,
          ),
        );
        productsBlock.append(detail);
      });
  });
  card.append(productsBlock);
  if (loadedChantier) {
    const actions = node("div", undefined, "strategy-choose");
    const choose = node("button", "Choisir cette solution", "button secondary");
    choose.type = "button";
    choose.dataset.strategyKey = option.key;
    choose.addEventListener("click", () => openChoiceConfirm(option));
    actions.append(choose);
    card.append(actions);
  }
  return card;
}

let lastComparison = null;
function renderComparison(data) {
  lastComparison = data;
  $("results").replaceChildren(...data.strategies.map(renderStrategy));
  const delta = data.minimum_vs_single;
  const summary = $("decision-summary");
  summary.hidden = false;
  summary.replaceChildren(
    node(
      "strong",
      `Départ et retour : ${data.origin.label}${data.origin.address ? " · " + data.origin.address : ""}`,
    ),
  );
  const signed = (value) => `${Number(value) > 0 ? "+" : ""}${number(value)}`;
  if (delta) {
    summary.append(
      node(
        "p",
        `Prix minimum par rapport à « 1 seul arrêt » : ${money(delta.material_savings)} d’économie matériaux · ${signed(delta.extra_travel_minutes)} min de trajet · ${signed(delta.extra_distance_km)} km · ${signed(delta.extra_total_minutes)} min préparation comprise.`,
      ),
    );
  } else
    summary.append(
      node(
        "p",
        "Comparaison des écarts indisponible : aucune solution complète en un seul arrêt.",
      ),
    );
  $("legacy-results").replaceChildren(
    ...data.options.map((option) => {
      const detail = node("details");
      detail.append(
        node(
          "summary",
          `${option.title} · ${option.valid ? money(option.total) + " HT" : "Panier incomplet"}`,
        ),
        node(
          "p",
          `${option.agency_count} arrêt(s) · Préparation max. ${option.max_preparation_minutes} min · ${option.available.length} ligne(s) disponible(s)`,
        ),
      );
      if (!option.valid)
        detail.append(
          node(
            "p",
            `Sous-total disponible : ${money(option.available_subtotal)} HT`,
          ),
        );
      option.available.forEach((line) =>
        detail.append(
          node(
            "p",
            `${line.product_name} · ${line.packs} × ${line.supplier_unit} · ${money(line.line_total)} HT · ${option.agencies.find((a) => a.id === line.agency_id).name}`,
          ),
        ),
      );
      option.unavailable.forEach((line) =>
        detail.append(node("p", `${line.product_name} : ${line.reason}`)),
      );
      return detail;
    }),
  );
}

let currentCoordinates = null;
let positionRequest = 0;
const savedAddresses = { site: $("origin-address").value, other: "" };
let previousOriginType = "site";
const originType = () =>
  document.querySelector('input[name="origin-type"]:checked').value;

function requestPosition() {
  if (originType() !== "current_location") return;
  const token = ++positionRequest;
  currentCoordinates = null;
  invalidate();
  if (!navigator.geolocation) {
    $("position-status").textContent =
      "Géolocalisation indisponible. Choisissez une adresse de démonstration.";
    return;
  }
  $("position-status").textContent =
    "Autorisez le navigateur à partager votre position pour cette comparaison…";
  navigator.geolocation.getCurrentPosition(
    (position) => {
      if (token !== positionRequest || originType() !== "current_location")
        return;
      currentCoordinates = {
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
      };
      $("position-status").textContent =
        `Position obtenue : ${number(currentCoordinates.latitude)}, ${number(currentCoordinates.longitude)}. Trajets simulés.`;
      invalidate();
    },
    () => {
      if (token !== positionRequest || originType() !== "current_location")
        return;
      $("position-status").textContent =
        "Position non obtenue ou autorisation refusée. Réessayez ou choisissez une adresse de démonstration.";
    },
    { enableHighAccuracy: false, timeout: 10000, maximumAge: 0 },
  );
}

document.querySelectorAll('input[name="origin-type"]').forEach((input) => {
  input.addEventListener("change", () => {
    if (["site", "other"].includes(previousOriginType))
      savedAddresses[previousOriginType] = $("origin-address").value;
    const type = originType();
    positionRequest++;
    currentCoordinates = null;
    $("address-field").hidden = !["site", "other"].includes(type);
    $("company-origin").hidden = type !== "company";
    $("position-origin").hidden = type !== "current_location";
    if (["site", "other"].includes(type))
      $("origin-address").value = savedAddresses[type];
    $("address-label").textContent =
      type === "other" ? "Autre adresse de départ" : "Adresse du chantier";
    previousOriginType = type;
    invalidate();
    if (type === "current_location") requestPosition();
  });
});
$("locate").addEventListener("click", requestPosition);
$("origin-address").addEventListener("input", () => {
  siteCoordinates = null;
  siteAddressNeedsConfirmation = false;
  invalidate();
});

function selectedOrigin() {
  const type = originType();
  if (type === "company") return { type };
  if (type === "current_location") {
    if (!currentCoordinates) {
      status(
        "Autorisez votre position ou choisissez une adresse avant de comparer.",
        true,
      );
      return null;
    }
    return { type, ...currentCoordinates };
  }
  if (!$("origin-address").reportValidity()) return null;
  const address = $("origin-address").value.trim();
  if (type === "site" && siteCoordinates) {
    return {
      type,
      latitude: siteCoordinates.latitude,
      longitude: siteCoordinates.longitude,
    };
  }
  if (type === "site" && siteAddressNeedsConfirmation && !isKnownDemoAddress(address)) {
    status(
      "Adresse du chantier inconnue du géocodeur de démonstration. " +
        "Choisissez une adresse de test proposée ou Ma position avant de comparer.",
      true,
    );
    return null;
  }
  return { type, address };
}

$("compare").addEventListener("click", async () => {
  if (!reportQuantityFields($("cart-body"))) return;
  if (!cart.length) return;
  const origin = selectedOrigin();
  if (!origin) return;
  const requestedRevision = revision;
  $("compare").disabled = true;
  $("results-section").hidden = true;
  status("Comparaison des offres simulées en cours…");
  try {
    const response = await fetch("/api/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lines: cart, origin }),
    });
    if (!response.ok) {
      const payload = await response.json();
      throw new Error(
        apiErrorMessage(
          payload,
          "Impossible de comparer ce panier. Vérifiez les produits et les quantités.",
        ),
      );
    }
    const data = await response.json();
    if (revision !== requestedRevision) return;
    comparisonFingerprint = materialsFingerprint(cart);
    renderComparison(data);
    $("results-section").hidden = false;
    status("Comparaison terminée. Les résultats sont affichés ci-dessous.");
    $("results-section").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    if (revision === requestedRevision)
      status(error.message || "Connexion impossible. Réessayez.", true);
  } finally {
    syncCompareButton();
  }
});

async function openChoiceConfirm(option) {
  if (!loadedChantier || !lastComparison || !option?.valid) return;
  if (!comparisonFingerprint || materialsFingerprint(cart) !== comparisonFingerprint) {
    status(STALE_COMPARISON_MESSAGE, true);
    return;
  }
  pendingChoice = option;
  let hasExisting = false;
  try {
    const existing = await fetch(
      `/api/chantiers/${loadedChantier.id}/approvisionnement`,
    );
    hasExisting = existing.status === 200;
  } catch {
    hasExisting = false;
  }
  const agencies = (option.stops || [])
    .map((stop) => `${stop.name} (${stop.supplier})`)
    .join(" → ");
  const body = $("choice-confirm-body");
  body.replaceChildren();
  $("choice-confirm-title").textContent =
    `Retenir cette solution pour le chantier « ${loadedChantier.nom} » ?`;
  body.append(
    node("p", `Stratégie : ${option.title}`),
    node("p", agencies ? `Agence(s) : ${agencies}` : "Aucune agence"),
    node(
      "p",
      `Total matériaux : ${money(option.material_total)} HT`,
    ),
  );
  if (option.estimated_procurement_cost != null) {
    body.append(
      node(
        "p",
        `Total estimé d’approvisionnement : ${money(option.estimated_procurement_cost)}`,
      ),
    );
  }
  if (hasExisting) {
    body.append(
      node(
        "p",
        "Ce chantier possède déjà un approvisionnement retenu. "
          + "Le remplacer par cette nouvelle solution ?",
        "status error",
      ),
    );
  }
  $("choice-confirm-panel").hidden = false;
  $("choice-confirm-panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

$("choice-confirm-cancel").addEventListener("click", () => {
  hideChoiceConfirm();
  status();
});

$("choice-confirm-submit").addEventListener("click", async () => {
  if (!loadedChantier || !pendingChoice || !lastComparison) return;
  if (!comparisonFingerprint || materialsFingerprint(cart) !== comparisonFingerprint) {
    hideChoiceConfirm();
    status(STALE_COMPARISON_MESSAGE, true);
    return;
  }
  const button = $("choice-confirm-submit");
  button.disabled = true;
  status("Enregistrement de l’approvisionnement…");
  try {
    const response = await fetch(
      `/api/chantiers/${loadedChantier.id}/approvisionnement`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          updated_at: loadedChantier.updated_at,
          needs_fingerprint: comparisonFingerprint,
          strategy: pendingChoice,
          origin: lastComparison.origin || null,
          cost_parameters: lastComparison.cost_parameters || null,
          currency: lastComparison.currency || "EUR",
          tax_basis: lastComparison.tax_basis || "HT",
        }),
      },
    );
    const payload = await response.json().catch(() => ({}));
    if (response.status === 409) {
      const detail =
        typeof payload.detail === "string"
          ? payload.detail
          : STALE_COMPARISON_MESSAGE;
      status(detail, true);
      return;
    }
    if (!response.ok) {
      throw new Error(
        apiErrorMessage(payload, "Impossible de retenir cette solution."),
      );
    }
    loadedChantier.updated_at = payload.chantier_updated_at;
    hideChoiceConfirm();
    status("Approvisionnement retenu enregistré.");
  } catch (error) {
    status(error.message || "Connexion impossible. Réessayez.", true);
  } finally {
    button.disabled = false;
  }
});

$("save-as-chantier").addEventListener("click", () => {
  if (cart.length === 0 || loadedChantier) return;
  openSaveAsPanel();
});

$("save-as-cancel").addEventListener("click", () => {
  hideSaveAsPanel();
  status();
});

$("save-as-adresse").addEventListener("input", () => {
  // Ne jamais conserver des coords d'une adresse précédente.
  $("save-as-latitude").value = "";
  $("save-as-longitude").value = "";
});

$("save-as-chantier-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (loadedChantier) return;
  if (!cart.length) {
    status("Ajoutez au moins un matériau avant d’enregistrer un chantier.", true);
    return;
  }
  if (!$("save-as-nom").reportValidity() || !$("save-as-adresse").reportValidity()) {
    return;
  }
  if (!reportQuantityFields($("cart-body"))) return;
  const submit = $("save-as-submit");
  submit.disabled = true;
  status("Enregistrement du chantier…");
  const lat = optionalText($("save-as-latitude").value);
  const lng = optionalText($("save-as-longitude").value);
  const body = {
    nom: $("save-as-nom").value.trim(),
    client: optionalText($("save-as-client").value),
    adresse: $("save-as-adresse").value.trim(),
    date_prevue: optionalText($("save-as-date").value),
    notes: optionalText($("save-as-notes").value),
    latitude: lat,
    longitude: lng,
    materiaux: cartMateriauxPayload(),
  };
  try {
    const response = await fetch("/api/chantiers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      let payload = null;
      try {
        payload = await response.json();
      } catch {
        payload = null;
      }
      throw new Error(
        apiErrorMessage(
          payload,
          "Impossible d’enregistrer ce chantier. Vérifiez le formulaire et les produits.",
        ),
      );
    }
    const created = await response.json();
    status("Chantier enregistré.");
    window.location.assign(`/chantiers/${created.id}`);
  } catch (error) {
    status(error.message || "Connexion impossible. Réessayez.", true);
  } finally {
    submit.disabled = false;
  }
});

$("update-chantier").addEventListener("click", async () => {
  if (!loadedChantier) return;
  if (!reportQuantityFields($("cart-body"))) return;
  if (cart.length === 0 && !window.confirm(EMPTY_UPDATE_CONFIRM)) return;
  const button = $("update-chantier");
  button.disabled = true;
  status("Mise à jour du chantier…");
  const body = {
    nom: loadedChantier.nom,
    client: loadedChantier.client,
    adresse: loadedChantier.adresse,
    date_prevue: loadedChantier.date_prevue,
    notes: loadedChantier.notes,
    latitude: loadedChantier.latitude,
    longitude: loadedChantier.longitude,
    updated_at: loadedChantier.updated_at,
    materiaux: cartMateriauxPayload(),
  };
  try {
    const response = await fetch(`/api/chantiers/${loadedChantier.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (response.status === 409) {
      status(UPDATE_CONFLICT_MESSAGE, true);
      return;
    }
    if (response.status === 404) {
      status("Ce chantier a été supprimé. Votre panier est conservé.", true);
      return;
    }
    if (!response.ok) {
      let payload = null;
      try {
        payload = await response.json();
      } catch {
        payload = null;
      }
      throw new Error(
        apiErrorMessage(
          payload,
          "Impossible de mettre à jour ce chantier. Votre panier est conservé.",
        ),
      );
    }
    const updated = await response.json();
    rememberLoadedChantier(updated);
    showChantierBanner(updated);
    status("Chantier mis à jour.");
  } catch (error) {
    status(error.message || "Connexion impossible. Réessayez.", true);
  } finally {
    syncChantierActions();
  }
});

async function init() {
  $("example").disabled = true;
  try {
    products = await fetchProducts();
    renderProducts();
    renderCart();
    $("example").disabled = !["PMC0001", "PMC0002", "PMC0003"].every((code) =>
      products.some((p) => p.code === code),
    );
    await loadChantierFromQuery();
  } catch (error) {
    fillProductSelect($("product"), []);
    $("product").replaceChildren(node("option", "Catalogue indisponible"));
    status(error.message, true);
  }
}

init();
