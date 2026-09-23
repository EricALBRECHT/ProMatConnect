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
function renderOption(option) {
  const card = node(
    "article",
    undefined,
    `result-card ${option.key === "optimized" ? "optimized" : ""}`,
  );
  card.append(
    node(
      "div",
      option.key === "optimized"
        ? "Le meilleur prix par ligne"
        : "Une seule enseigne",
      "card-label",
    ),
  );
  card.append(node("h3", option.title));
  card.append(
    node(
      "span",
      option.valid
        ? "Panier complet disponible"
        : `${option.unavailable.length} produit(s) indisponible(s)`,
      `badge ${option.valid ? "" : "warning"}`,
    ),
  );
  const price = node(
    "p",
    option.valid ? money(option.total) : "Panier incomplet",
    "price",
  );
  if (option.valid) price.append(node("small", " HT"));
  card.append(price);
  if (!option.valid)
    card.append(
      node(
        "p",
        `Sous-total disponible : ${money(option.available_subtotal)} HT`,
        "subtotal-note",
      ),
    );
  const metrics = node("dl", undefined, "metrics");
  [
    [
      "Disponibilité",
      `${option.available.length}/${option.available.length + option.unavailable.length} lignes`,
    ],
    ["Fournisseurs", String(option.supplier_count)],
    ["Nombre d’arrêts", String(option.agency_count)],
    [
      "Préparation max.",
      option.available.length ? `${option.max_preparation_minutes} min` : "—",
    ],
  ].forEach(([label, value]) => {
    const group = node("div");
    group.append(node("dt", label), node("dd", value));
    metrics.append(group);
  });
  card.append(metrics);
  const agencies = node("ul", undefined, "agency-list");
  option.agencies.forEach((agency) => {
    const item = node("li");
    item.append(
      node("strong", `${agency.name} · ≈ ${number(agency.distance_km)} km`),
      node("span", `${agency.address}, ${agency.postal_code} ${agency.city}`),
    );
    agencies.append(item);
  });
  card.append(agencies);
  const details = node("details");
  details.append(node("summary", "Voir le détail des matériaux"));
  option.available.forEach((line) => {
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
      node("p", option.agencies.find((a) => a.id === line.agency_id).name),
      node(
        "p",
        `Stock : ${number(line.available_quantity)} ${line.reference_unit} · Préparation : ${line.preparation_minutes} min`,
      ),
      node(
        "small",
        `Offre fictive du ${new Date(line.updated_at).toLocaleDateString("fr-FR")}`,
      ),
    );
    details.append(detail);
  });
  option.unavailable.forEach((line) => {
    const detail = node("div", undefined, "detail-line");
    detail.append(
      node("strong", line.product_name, "unavailable"),
      node("p", line.reason),
    );
    details.append(detail);
  });
  card.append(details);
  return card;
}
$("compare").addEventListener("click", async () => {
  for (const input of $("cart-body").querySelectorAll("input")) {
    if (!input.reportValidity()) return;
  }
  if (!cart.length) return;
  const requestedRevision = revision;
  $("compare").disabled = true;
  $("results-section").hidden = true;
  status("Comparaison des offres simulées en cours…");
  try {
    const response = await fetch("/api/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lines: cart }),
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
    $("results").replaceChildren(...data.options.map(renderOption));
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
