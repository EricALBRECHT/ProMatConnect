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
      const quantity = Number(quantityInput.value);
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
        setLines([...lines, { product_id: id, quantity: String(quantity) }]);
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
    bindMaterialLines,
  };
})();
