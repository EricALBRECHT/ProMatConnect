"use strict";
const $ = (id) => document.getElementById(id);
let products = [];
let cart = [];
let revision = 0;
const money = (value) =>
  new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR" }).format(
    value,
  );
const number = (value) =>
  new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 3 }).format(value);
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function status(message = "", error = false) {
  $("status").textContent = message;
  $("status").className = error ? "status error" : "status";
}
function invalidate() {
  revision++;
  $("results-section").hidden = true;
  status();
}
function renderProducts() {
  const query = $("search").value.trim().toLocaleLowerCase("fr");
  const filtered = products.filter((p) =>
    `${p.name} ${p.code} ${p.category}`.toLocaleLowerCase("fr").includes(query),
  );
  $("product").replaceChildren(
    ...filtered.map((p) => {
      const option = node("option", `${p.name} · ${p.reference_unit}`);
      option.value = p.id;
      return option;
    }),
  );
  if (!filtered.length) {
    const option = node("option", "Aucun matériau trouvé");
    option.value = "";
    $("product").append(option);
  }
}
function renderCart() {
  $("cart-body").replaceChildren();
  cart.forEach((line) => {
    const product = products.find((p) => p.id === line.product_id);
    const row = node("tr");
    const name = node("td", product.name);
    name.append(node("small", `${product.code} · ${product.category}`));
    const quantityCell = node("td");
    const input = node("input", undefined, "line-quantity");
    Object.assign(input, {
      type: "number",
      min: "0.001",
      max: "1000000",
      step: "0.001",
      value: line.quantity,
      required: true,
    });
    input.setAttribute("aria-label", `Quantité pour ${product.name}`);
    input.addEventListener("input", () => {
      line.quantity = input.value;
      invalidate();
    });
    quantityCell.append(input);
    const removeCell = node("td");
    const remove = node("button", "×", "remove");
    remove.type = "button";
    remove.setAttribute("aria-label", `Supprimer ${product.name}`);
    remove.addEventListener("click", () => {
      cart = cart.filter((l) => l !== line);
      invalidate();
      renderCart();
    });
    removeCell.append(remove);
    row.append(
      name,
      quantityCell,
      node("td", product.reference_unit),
      removeCell,
    );
    $("cart-body").append(row);
  });
  $("empty-cart").hidden = cart.length > 0;
  $("line-count").textContent =
    `${cart.length} produit${cart.length > 1 ? "s" : ""}`;
  $("compare").disabled = cart.length === 0;
}
$("search").addEventListener("input", renderProducts);
$("add-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const id = Number($("product").value);
  if (!id) return;
  const existing = cart.find((l) => l.product_id === id);
  const quantity = Number($("quantity").value);
  if (existing) {
    const combined =
      Math.round((Number(existing.quantity) + quantity) * 1000) / 1000;
    if (combined > 1000000) {
      status("La quantité maximale par produit est de 1 000 000.", true);
      return;
    }
    existing.quantity = String(combined);
  } else cart.push({ product_id: id, quantity: String(quantity) });
  invalidate();
  renderCart();
});
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
  const products = node("details");
  products.append(node("summary", "Matériaux par agence"));
  option.stops.forEach((stop) => {
    products.append(
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
        products.append(detail);
      });
  });
  card.append(products);
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
    // Appel uniquement après choix explicite de « Ma position », jamais au chargement.
    if (type === "current_location") requestPosition();
  });
});
$("locate").addEventListener("click", requestPosition);
$("origin-address").addEventListener("input", invalidate);
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
  return { type, address: $("origin-address").value.trim() };
}
$("compare").addEventListener("click", async () => {
  for (const input of $("cart-body").querySelectorAll("input")) {
    if (!input.reportValidity()) return;
  }
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
        typeof payload.detail === "string"
          ? payload.detail
          : "Impossible de comparer ce panier. Vérifiez les produits et les quantités.",
      );
    }
    const data = await response.json();
    if (revision !== requestedRevision) return;
    renderComparison(data);
    $("results-section").hidden = false;
    status("Comparaison terminée. Les résultats sont affichés ci-dessous.");
    $("results-section").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    if (revision === requestedRevision)
      status(error.message || "Connexion impossible. Réessayez.", true);
  } finally {
    $("compare").disabled = !cart.length;
  }
});
async function init() {
  $("example").disabled = true;
  try {
    const response = await fetch("/api/products");
    if (!response.ok)
      throw new Error(
        "Catalogue indisponible. Rechargez la page pour réessayer.",
      );
    products = await response.json();
    renderProducts();
    renderCart();
    $("example").disabled = !["PMC0001", "PMC0002", "PMC0003"].every((code) =>
      products.some((p) => p.code === code),
    );
  } catch (error) {
    $("product").replaceChildren(node("option", "Catalogue indisponible"));
    status(error.message, true);
  }
}
init();
