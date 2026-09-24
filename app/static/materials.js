"use strict";

window.ProMatMaterials = (() => {
  const $ = (id) => document.getElementById(id);

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  function filterProducts(products, query) {
    const needle = query.trim().toLocaleLowerCase("fr");
    if (!needle) return products.slice();
    return products.filter((product) =>
      `${product.name} ${product.code} ${product.category}`
        .toLocaleLowerCase("fr")
        .includes(needle),
    );
  }

  function fillProductSelect(select, products, query = "") {
    const filtered = filterProducts(products, query);
    select.replaceChildren(
      ...filtered.map((product) => {
        const option = node(
          "option",
          `${product.name} · ${product.reference_unit}`,
        );
        option.value = product.id;
        return option;
      }),
    );
    if (!filtered.length) {
      const option = node("option", "Aucun matériau trouvé");
      option.value = "";
      select.append(option);
    }
  }

  async function fetchProducts() {
    const response = await fetch("/api/products");
    if (!response.ok) {
      throw new Error(
        "Catalogue indisponible. Rechargez la page pour réessayer.",
      );
    }
    return response.json();
  }

  function apiErrorMessage(payload, fallback) {
    if (!payload || payload.detail === undefined) return fallback;
    if (typeof payload.detail === "string") return payload.detail;
    if (payload.detail && typeof payload.detail.message === "string") {
      return payload.detail.message;
    }
    return fallback;
  }

  function parseQuantity(raw) {
    const normalized = String(raw ?? "")
      .trim()
      .replace(",", ".");
    if (!normalized) return { ok: false, reason: "empty" };
    const value = Number(normalized);
    if (!Number.isFinite(value)) return { ok: false, reason: "nan" };
    if (value < 0.001 || value > 1000000) return { ok: false, reason: "range" };
    const fraction = normalized.includes(".") ? normalized.split(".")[1] : "";
    if (fraction.length > 3) return { ok: false, reason: "decimals" };
    return { ok: true, value, text: normalized };
  }

  /**
   * step=1 : flèches natives ±1.
   * min HTML absent : avec min=0.001 et step=1, 2→2.001 (base de pas).
   * Le plancher métier 0.001 reste contrôlé dans reportQuantityValidity.
   */
  function configureQuantityInput(input, { value, ariaLabel } = {}) {
    input.type = "number";
    input.removeAttribute("min");
    input.max = "1000000";
    input.step = "1";
    input.required = true;
    if (value !== undefined) input.value = value;
    if (ariaLabel) input.setAttribute("aria-label", ariaLabel);
    return input;
  }

  function reportQuantityValidity(input) {
    const parsed = parseQuantity(input.value);
    if (!parsed.ok) {
      const messages = {
        empty: "Renseignez une quantité.",
        nan: "Indiquez une quantité numérique valide.",
        range: "Indiquez une quantité entre 0,001 et 1 000 000.",
        decimals: "Trois décimales maximum.",
      };
      input.setCustomValidity(messages[parsed.reason] || messages.nan);
      const ok = input.reportValidity();
      input.setCustomValidity("");
      return ok;
    }
    input.setCustomValidity("");
    // Harmonise une éventuelle virgule collée hors type=number.
    if (String(input.value).includes(",")) input.value = parsed.text;
    // step=1 pilote les flèches ; "any" évite le rejet HTML des décimales saisies.
    const previousStep = input.step;
    input.step = "any";
    const ok = input.reportValidity();
    input.step = previousStep;
    return ok;
  }

  function reportQuantityFields(container) {
    const inputs = container.querySelectorAll(
      'input.line-quantity, input#quantity, input[type="number"].line-quantity',
    );
    for (const input of inputs) {
      if (!reportQuantityValidity(input)) return false;
    }
    return true;
  }

  /**
   * Liste de matériaux partagée (comparateur et chantiers).
   * lines : [{ product_id, quantity }] avec quantity en chaîne.
   */
  function bindMaterialLines({
    getProducts,
    getLines,
    setLines,
    onChange,
    searchId = "search",
    productId = "product",
    quantityId = "quantity",
    formId = "add-form",
    bodyId = "cart-body",
    emptyId = "empty-cart",
    countId = "line-count",
    status,
  }) {
    const search = $(searchId);
    const productSelect = $(productId);
    const quantityInput = $(quantityId);
    const form = $(formId);
    const body = $(bodyId);
    const empty = $(emptyId);
    const count = $(countId);

    if (quantityInput) configureQuantityInput(quantityInput);

    function renderProducts() {
      fillProductSelect(productSelect, getProducts(), search.value);
    }

    function renderLines() {
      const products = getProducts();
      const lines = getLines();
      body.replaceChildren();
      lines.forEach((line) => {
        const product = products.find((item) => item.id === line.product_id);
        if (!product) return;
        const row = node("tr");
        const name = node("td", product.name);
        name.append(node("small", `${product.code} · ${product.category}`));
        const quantityCell = node("td");
        const input = configureQuantityInput(node("input", undefined, "line-quantity"), {
          value: line.quantity,
          ariaLabel: `Quantité pour ${product.name}`,
        });
        input.addEventListener("input", () => {
          line.quantity = input.value;
          onChange();
        });
        quantityCell.append(input);
        const removeCell = node("td");
        const remove = node("button", "×", "remove");
        remove.type = "button";
        remove.setAttribute("aria-label", `Supprimer ${product.name}`);
        remove.addEventListener("click", () => {
          setLines(getLines().filter((item) => item !== line));
          onChange();
          renderLines();
        });
        removeCell.append(remove);
        row.append(
          name,
          quantityCell,
          node("td", product.reference_unit),
          removeCell,
        );
        body.append(row);
      });
      empty.hidden = lines.length > 0;
      count.textContent = `${lines.length} produit${lines.length > 1 ? "s" : ""}`;
    }

    search.addEventListener("input", renderProducts);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const id = Number(productSelect.value);
      if (!id) return;
      if (!reportQuantityValidity(quantityInput)) return;
      const parsed = parseQuantity(quantityInput.value);
      const quantity = parsed.value;
      const lines = getLines();
      const existing = lines.find((line) => line.product_id === id);
      if (existing) {
        const combined =
          Math.round((Number(existing.quantity) + quantity) * 1000) / 1000;
        if (combined > 1000000) {
          if (status) {
            status("La quantité maximale par produit est de 1 000 000.", true);
          }
          return;
        }
        existing.quantity = String(combined);
      } else {
        setLines([
          ...lines,
          { product_id: id, quantity: String(quantity) },
        ]);
      }
      onChange();
      renderLines();
    });

    return { renderProducts, renderLines };
  }

  return {
    $,
    node,
    filterProducts,
    fillProductSelect,
    fetchProducts,
    apiErrorMessage,
    parseQuantity,
    configureQuantityInput,
    reportQuantityValidity,
    reportQuantityFields,
    bindMaterialLines,
  };
})();
