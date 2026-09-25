(() => {
  const root = document.querySelector("[data-admin-suppliers]");
  if (!root) return;

  const PLACEHOLDER = "/static/product-placeholder.svg";
  const fileInput = document.getElementById("supplier-csv");
  const previewBtn = document.getElementById("supplier-preview-btn");
  const commitBtn = document.getElementById("supplier-commit-btn");
  const previewBox = document.getElementById("supplier-preview");
  const mappingSection = document.getElementById("catalog-mapping-section");
  const mappingPanel = document.getElementById("catalog-mapping-panel");
  const mappingMeta = document.getElementById("catalog-mapping-meta");

  let lastFile = null;
  let lastPreviewOk = false;
  /** @type {Map<string, number|null>} */
  let previewMappings = new Map();
  /** @type {Array<object>} */
  let currentRows = [];
  let mode = "preview"; // preview | catalog
  let catalogContext = null;

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function mapKey(supplier, ref) {
    return `${supplier}||${ref}`;
  }

  function formatDetail(detail) {
    if (detail == null) return "Action impossible";
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) =>
          typeof item === "string"
            ? item
            : item?.msg || item?.message || JSON.stringify(item)
        )
        .join(" · ");
    }
    if (typeof detail === "object") {
      if (detail.message) return detail.message;
      if (Array.isArray(detail.errors)) {
        return detail.errors
          .map((e) => (e.line ? `Ligne ${e.line}: ` : "") + (e.message || e.code || ""))
          .join(" · ");
      }
      try {
        return JSON.stringify(detail);
      } catch {
        return "Action impossible";
      }
    }
    return String(detail);
  }

  function productThumb(url, name) {
    const safeName = esc(name || "Produit");
    const src = url ? esc(url) : PLACEHOLDER;
    return `<img class="product-thumb" src="${src}" alt="${safeName}" width="56" height="56" loading="lazy" decoding="async" onerror="this.onerror=null;this.src='${PLACEHOLDER}'" />`;
  }

  function packagingLabel(row) {
    const unit = row.supplier_unit || "—";
    const qty = row.reference_quantity || row.packaging_quantity || "";
    const ref = row.reference_unit || "";
    return qty ? `${esc(qty)} ${esc(ref || unit)}` : esc(unit);
  }

  function priceCellHtml(row) {
    if (row.price == null || row.price === "") return "<em>sans prix</em>";
    const basis = row.tax_basis || "";
    let html = `${esc(row.price)}&nbsp;€&nbsp;${esc(basis)}`;
    if (row.vat_rate != null && row.vat_rate !== "") {
      html += `<div class="muted">TVA ${esc(row.vat_rate)}&nbsp;%</div>`;
      const price = Number(String(row.price).replace(",", "."));
      const vat = Number(String(row.vat_rate).replace(",", "."));
      if (Number.isFinite(price) && Number.isFinite(vat) && vat >= 0) {
        let equiv = null;
        let label = "";
        if (basis === "TTC") {
          equiv = price / (1 + vat / 100);
          label = "HT";
        } else if (basis === "HT") {
          equiv = price * (1 + vat / 100);
          label = "TTC";
        }
        if (equiv != null) {
          const shown = equiv.toLocaleString("fr-FR", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
          });
          html += `<div class="muted">≈ ${shown}&nbsp;€&nbsp;${label}</div>`;
        }
      }
    } else if (basis === "TTC" || basis === "HT") {
      html += `<div class="muted">Conversion HT/TTC indisponible : taux de TVA inconnu.</div>`;
    }
    return html;
  }

  function selectedProductHtml(id, code, name) {
    if (id == null) {
      return `<div class="map-selected is-empty"><em>Non mappé</em> — exclu du comparateur</div>`;
    }
    return `<div class="map-selected is-set">
      <span class="map-check">✓</span>
      <div>
        <strong class="map-code">${esc(code || "#" + id)}</strong>
        <div class="map-name">${esc(name || "")}</div>
      </div>
      <button type="button" class="button secondary product-clear" title="Retirer">× Retirer</button>
    </div>`;
  }

  function rowMatchesFilter(row, filter, query) {
    const key = mapKey(row.supplier, row.external_reference);
    const pid = previewMappings.has(key) ? previewMappings.get(key) : row.product_id;
    const mapped = pid != null;
    const hasPrice = row.price != null && row.price !== "";
    if (filter === "unmapped" && mapped) return false;
    if (filter === "mapped" && !mapped) return false;
    if (filter === "with_price" && !hasPrice) return false;
    if (filter === "without_price" && hasPrice) return false;
    if (filter === "leroy" && !(row.supplier || "").toLowerCase().includes("leroy")) return false;
    if (filter === "brico" && !(row.supplier || "").toLowerCase().includes("brico")) return false;
    if (query) {
      const hay = `${row.external_reference} ${row.name} ${row.brand || ""} ${row.supplier}`.toLowerCase();
      if (!hay.includes(query)) return false;
    }
    return true;
  }

  function countsFor(rows) {
    let mapped = 0;
    rows.forEach((row) => {
      const key = mapKey(row.supplier, row.external_reference);
      const pid = previewMappings.has(key) ? previewMappings.get(key) : row.product_id;
      if (pid != null) mapped += 1;
    });
    return { total: rows.length, mapped, todo: rows.length - mapped };
  }

  function renderToolbar(rows) {
    const c = countsFor(rows);
    const suppliers = [...new Set(rows.map((r) => r.supplier).filter(Boolean))].sort();
    const supplierFilters = suppliers
      .map((s) => {
        const key = s.toLowerCase().includes("leroy")
          ? "leroy"
          : s.toLowerCase().includes("brico")
            ? "brico"
            : "";
        if (!key) return "";
        return `<button type="button" class="chip filter-chip" data-filter="${key}">${esc(s)}</button>`;
      })
      .join("");
    return `
      <div class="map-toolbar">
        <div class="map-counters" data-map-counters>
          <strong>${c.total}</strong> références ·
          <strong data-c-mapped>${c.mapped}</strong> mappée(s) ·
          <strong data-c-todo>${c.todo}</strong> à traiter
        </div>
        <div class="map-filters">
          <input type="search" class="map-filter-search" placeholder="Recherche fournisseur / réf. / nom…" />
          <button type="button" class="chip filter-chip is-active" data-filter="all">Tous</button>
          <button type="button" class="chip filter-chip" data-filter="unmapped">Non mappés</button>
          <button type="button" class="chip filter-chip" data-filter="mapped">Mappés</button>
          <button type="button" class="chip filter-chip" data-filter="with_price">Avec prix</button>
          <button type="button" class="chip filter-chip" data-filter="without_price">Sans prix</button>
          ${supplierFilters}
        </div>
        <div class="map-bulk">
          <button type="button" class="button secondary" id="map-bulk-apply" disabled>
            Associer le Product aux lignes cochées
          </button>
          <button type="button" class="button secondary" id="map-create-product">
            + Créer un Product ProMatConnect
          </button>
        </div>
      </div>
    `;
  }

  function renderRowDesktop(row) {
    const key = mapKey(row.supplier, row.external_reference);
    const currentId = previewMappings.has(key) ? previewMappings.get(key) : row.product_id;
    const code = row.product_code || "";
    const name = row.product_name || "";
    return `<tr class="map-row" data-key="${esc(key)}" data-supplier="${esc(row.supplier)}" data-ref="${esc(row.external_reference)}" data-sp-id="${esc(row.supplier_product_id || "")}">
      <td class="map-check-col"><input type="checkbox" class="map-select" /></td>
      <td class="map-thumb-col">${productThumb(row.image_url, row.name)}</td>
      <td class="map-ref-col">
        <code>${esc(row.external_reference)}</code>
        <div class="map-supplier-name">${esc(row.name)}</div>
        <div class="muted map-supplier-label">${esc(row.supplier)}</div>
      </td>
      <td class="map-brand-col">${esc(row.brand || "—")}</td>
      <td class="map-pack-col">${packagingLabel(row)}</td>
      <td class="map-price-col">${priceCellHtml(row)}</td>
      <td class="map-product-col">
        <div class="product-picker" data-map-key="${esc(key)}" data-sp-id="${esc(row.supplier_product_id || "")}">
          ${selectedProductHtml(currentId, code, name)}
          <input type="search" class="product-search" placeholder="Rechercher un Product (code ou nom)…" autocomplete="off" />
          <input type="hidden" class="product-id-value" value="${currentId != null ? esc(currentId) : ""}" />
          <ul class="product-suggestions" hidden></ul>
        </div>
      </td>
      <td class="map-actions-col">
        ${row.supplier_product_id
          ? `<button type="button" class="button secondary map-edit-conditioning" data-sp-id="${esc(row.supplier_product_id)}">Modifier</button>`
          : ""}
        ${
          row.unit_anomaly
            ? `<div class="map-anomaly">Unité incompatible — offre exclue du comparateur</div>`
            : ""
        }
        ${
          row.correction_source === "manual"
            ? `<div class="muted map-override-flag">Correction manuelle</div>`
            : ""
        }
      </td>
    </tr>`;
  }

  function renderRowCard(row) {
    const key = mapKey(row.supplier, row.external_reference);
    const currentId = previewMappings.has(key) ? previewMappings.get(key) : row.product_id;
    const code = row.product_code || "";
    const name = row.product_name || "";
    return `<article class="map-card" data-key="${esc(key)}" data-supplier="${esc(row.supplier)}" data-ref="${esc(row.external_reference)}" data-sp-id="${esc(row.supplier_product_id || "")}">
      <label class="map-card-check"><input type="checkbox" class="map-select" /> Sélectionner</label>
      <div class="map-card-head">
        ${productThumb(row.image_url, row.name)}
        <div>
          <strong>${esc(row.name)}</strong>
          <div><code>${esc(row.external_reference)}</code></div>
          <div class="muted">${esc(row.supplier)}${row.brand ? " · " + esc(row.brand) : ""}</div>
        </div>
      </div>
      <dl class="map-card-facts">
        <div><dt>Conditionnement</dt><dd>${packagingLabel(row)}</dd></div>
        <div><dt>Prix</dt><dd>${
          row.price != null && row.price !== ""
            ? priceCellHtml(row)
            : "sans prix"
        }</dd></div>
      </dl>
      <div class="product-picker" data-map-key="${esc(key)}" data-sp-id="${esc(row.supplier_product_id || "")}">
        <p class="map-card-label">Product ProMatConnect</p>
        ${selectedProductHtml(currentId, code, name)}
        <input type="search" class="product-search" placeholder="Rechercher un Product…" autocomplete="off" />
        <input type="hidden" class="product-id-value" value="${currentId != null ? esc(currentId) : ""}" />
        <ul class="product-suggestions" hidden></ul>
      </div>
      <div class="map-card-actions">
        ${row.supplier_product_id
          ? `<button type="button" class="button secondary map-edit-conditioning" data-sp-id="${esc(row.supplier_product_id)}">Modifier</button>`
          : ""}
        ${
          row.unit_anomaly
            ? `<p class="map-anomaly">Unité incompatible — offre exclue du comparateur</p>`
            : ""
        }
        ${
          row.correction_source === "manual"
            ? `<p class="muted">Correction manuelle</p>`
            : ""
        }
      </div>
    </article>`;
  }

  function renderMappingWorkspace(rows) {
    currentRows = rows;
    rows.forEach((row) => {
      const key = mapKey(row.supplier, row.external_reference);
      if (!previewMappings.has(key)) previewMappings.set(key, row.product_id ?? null);
    });
    return `
      <div class="map-workspace" data-map-workspace>
        ${renderToolbar(rows)}
        <div class="map-desktop-wrap">
          <table class="admin-table map-table">
            <thead>
              <tr>
                <th></th>
                <th></th>
                <th>Référence / produit fournisseur</th>
                <th>Marque</th>
                <th>Cond.</th>
                <th>Prix</th>
                <th>Product ProMatConnect</th>
                <th></th>
              </tr>
            </thead>
            <tbody>${rows.map(renderRowDesktop).join("")}</tbody>
          </table>
        </div>
        <div class="map-cards">${rows.map(renderRowCard).join("")}</div>
        <p class="muted admin-mapping-note">
          Aucune association automatique. Plusieurs références peuvent viser le même Product.
          Cochez des lignes puis « Associer… » pour un mapping groupé explicite.
        </p>
      </div>
      <dialog class="map-create-dialog" id="map-create-dialog">
        <form method="dialog" class="map-create-form" id="map-create-form">
          <h3>Créer un Product ProMatConnect</h3>
          <p class="muted">Création explicite — aucun préremplissage depuis le libellé fournisseur.</p>
          <label>Code <input name="code" required pattern="PMC-[A-Z0-9-]+" placeholder="PMC-BA13-STD-2500X1200" /></label>
          <label>Nom <input name="name" required maxlength="200" /></label>
          <label>Catégorie <input name="category" required maxlength="80" /></label>
          <label>Sous-catégorie <input name="subcategory" maxlength="80" /></label>
          <label>Unité besoin
            <select name="reference_unit" required>
              <option value="pièce">pièce</option>
              <option value="m">m</option>
              <option value="m²">m²</option>
              <option value="kg">kg</option>
              <option value="L">L</option>
              <option value="sac">sac</option>
              <option value="rouleau">rouleau</option>
            </select>
          </label>
          <label>Description <textarea name="description" maxlength="500" rows="2"></textarea></label>
          <div class="map-create-actions">
            <button type="submit" class="button" value="create">Créer</button>
            <button type="submit" class="button secondary" value="cancel">Annuler</button>
          </div>
          <p class="error" data-create-error hidden></p>
        </form>
      </dialog>
      <dialog class="map-edit-dialog" id="map-edit-dialog">
        <form method="dialog" class="map-edit-form" id="map-edit-form">
          <h3>Modifier conditionnement</h3>
          <p class="muted" data-edit-meta></p>
          <div data-edit-product-attrs class="map-edit-attrs muted"></div>
          <p data-edit-anomaly class="map-anomaly" hidden></p>
          <label>Unité fournisseur (supplier_unit)
            <input name="supplier_unit" required maxlength="40" placeholder="lot / plaque / boîte" />
          </label>
          <label>Quantité par pack (packaging_quantity)
            <input name="packaging_quantity" type="number" step="0.001" min="0.001" required />
          </label>
          <label>Unité besoin (reference_unit)
            <select name="reference_unit" required>
              <option value="pièce">pièce</option>
              <option value="m">m</option>
              <option value="m²">m²</option>
              <option value="kg">kg</option>
              <option value="L">L</option>
            </select>
          </label>
          <label>Contenu en unités besoin (reference_quantity)
            <input name="reference_quantity" type="number" step="0.001" min="0.001" required />
          </label>
          <fieldset class="map-edit-tax">
            <legend>Prix source / TVA</legend>
            <label>Prix source
              <input name="price" type="number" step="0.01" min="0" inputmode="decimal" />
            </label>
            <label>Base
              <select name="tax_basis">
                <option value="HT">HT</option>
                <option value="TTC">TTC</option>
              </select>
            </label>
            <label>TVA
              <span class="map-edit-vat-row">
                <input name="vat_rate" type="number" step="0.01" min="0" max="100" inputmode="decimal" placeholder="ex. 20" />
                <span>%</span>
              </span>
            </label>
            <p class="muted" data-edit-tax-calc></p>
          </fieldset>
          <p class="muted" data-edit-hint></p>
          <p class="muted" data-edit-price></p>
          <div class="map-create-actions">
            <button type="submit" class="button" value="save">Enregistrer</button>
            <button type="submit" class="button secondary" value="cancel">Annuler</button>
            <button type="submit" class="button secondary" value="clear-override" data-clear-override hidden>
              Lever l'override
            </button>
          </div>
          <p class="error" data-edit-error hidden></p>
        </form>
      </dialog>
    `;
  }

  function updateCounters(workspace) {
    const c = countsFor(currentRows);
    const box = workspace.querySelector("[data-map-counters]");
    if (!box) return;
    box.innerHTML = `<strong>${c.total}</strong> références ·
      <strong>${c.mapped}</strong> mappée(s) ·
      <strong>${c.todo}</strong> à traiter`;
  }

  function applyFilters(workspace) {
    const filter =
      workspace.querySelector(".filter-chip.is-active")?.dataset.filter || "all";
    const query = (workspace.querySelector(".map-filter-search")?.value || "")
      .trim()
      .toLowerCase();
    workspace.querySelectorAll(".map-row, .map-card").forEach((el) => {
      const key = el.dataset.key;
      const row = currentRows.find(
        (r) => mapKey(r.supplier, r.external_reference) === key
      );
      el.hidden = !(row && rowMatchesFilter(row, filter, query));
    });
  }

  async function searchProducts(query) {
    const q = encodeURIComponent(query.trim());
    const res = await fetch(`/api/products?q=${q}&limit=25&for_mapping=true`);
    if (!res.ok) return [];
    return res.json();
  }

  function bindProductPickers(workspace) {
    workspace.querySelectorAll(".product-picker").forEach((picker) => {
      const input = picker.querySelector(".product-search");
      const hidden = picker.querySelector(".product-id-value");
      const list = picker.querySelector(".product-suggestions");
      const rowKey = picker.dataset.mapKey;
      let timer = null;

      function paintSelected(id, code, name) {
        hidden.value = id != null ? String(id) : "";
        const block = picker.querySelector(".map-selected");
        if (block) {
          block.outerHTML = selectedProductHtml(id, code, name);
        }
      }

      async function persist(id, code, name) {
        previewMappings.set(rowKey, id);
        paintSelected(id, code, name);
        updateCounters(workspace);
        const spId = picker.dataset.spId;
        if (mode === "catalog" && spId) {
          const res = await fetch(`/api/supplier-products/${spId}/mapping`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ product_id: id }),
          });
          if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            window.alert(formatDetail(body.detail || body));
          }
        }
        // Sync twin row/card picker
        workspace.querySelectorAll(`.product-picker[data-map-key="${CSS.escape(rowKey)}"]`).forEach((other) => {
          if (other === picker) return;
          const h = other.querySelector(".product-id-value");
          if (h) h.value = id != null ? String(id) : "";
          const sel = other.querySelector(".map-selected");
          if (sel) sel.outerHTML = selectedProductHtml(id, code, name);
        });
      }

      input.addEventListener("input", () => {
        clearTimeout(timer);
        const q = input.value.trim();
        if (q.length < 1) {
          list.hidden = true;
          list.innerHTML = "";
          return;
        }
        timer = setTimeout(async () => {
          const products = await searchProducts(q);
          if (!products.length) {
            list.innerHTML = "<li class='muted'>Aucun Product (hors legacy)</li>";
            list.hidden = false;
            return;
          }
          list.innerHTML = products
            .map(
              (p) => `<li data-id="${esc(p.id)}" data-code="${esc(p.code)}" data-name="${esc(p.name)}">
                <strong>${esc(p.code)}</strong>
                <span>${esc(p.name)}</span>
                <small class="muted">${esc(p.category)}${p.subcategory ? " · " + esc(p.subcategory) : ""} · ${esc(p.reference_unit)}</small>
              </li>`
            )
            .join("");
          list.hidden = false;
        }, 180);
      });

      list.addEventListener("mousedown", (event) => {
        event.preventDefault();
        const li = event.target.closest("li[data-id]");
        if (!li) return;
        persist(Number(li.dataset.id), li.dataset.code, li.dataset.name);
        list.hidden = true;
        input.value = "";
      });

      picker.addEventListener("click", (event) => {
        const btn = event.target.closest(".product-clear");
        if (!btn) return;
        persist(null, "", "");
      });
    });
  }

  function bindWorkspace(workspace) {
    bindProductPickers(workspace);
    workspace.querySelectorAll(".filter-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        workspace.querySelectorAll(".filter-chip").forEach((c) => c.classList.remove("is-active"));
        chip.classList.add("is-active");
        applyFilters(workspace);
      });
    });
    workspace.querySelector(".map-filter-search")?.addEventListener("input", () => {
      applyFilters(workspace);
    });

    const bulkBtn = workspace.querySelector("#map-bulk-apply");
    const syncBulk = () => {
      const n = workspace.querySelectorAll(".map-select:checked").length;
      if (bulkBtn) bulkBtn.disabled = n === 0;
    };
    workspace.addEventListener("change", (event) => {
      if (event.target.classList.contains("map-select")) syncBulk();
    });

    bulkBtn?.addEventListener("click", async () => {
      const checked = [...workspace.querySelectorAll(".map-row .map-select:checked, .map-card .map-select:checked")];
      // Prefer desktop rows if visible
      const targets = checked
        .map((cb) => cb.closest("[data-key]"))
        .filter(Boolean);
      const uniqueKeys = [...new Set(targets.map((t) => t.dataset.key))];
      if (!uniqueKeys.length) return;
      const samplePicker = workspace.querySelector(
        `.product-picker[data-map-key="${CSS.escape(uniqueKeys[0])}"] .product-id-value`
      );
      // Ask user to pick product once
      const q = window.prompt(
        "Code Product à appliquer aux lignes cochées (ex. PMC-BA13-STD-2500X1200) :"
      );
      if (!q) return;
      const found = await searchProducts(q.trim());
      const exact =
        found.find((p) => p.code.toLowerCase() === q.trim().toLowerCase()) || found[0];
      if (!exact) {
        window.alert("Product introuvable dans le référentiel (hors legacy).");
        return;
      }
      for (const key of uniqueKeys) {
        previewMappings.set(key, exact.id);
        workspace.querySelectorAll(`.product-picker[data-map-key="${CSS.escape(key)}"]`).forEach((picker) => {
          const h = picker.querySelector(".product-id-value");
          if (h) h.value = String(exact.id);
          const sel = picker.querySelector(".map-selected");
          if (sel) sel.outerHTML = selectedProductHtml(exact.id, exact.code, exact.name);
          const spId = picker.dataset.spId;
          if (mode === "catalog" && spId) {
            fetch(`/api/supplier-products/${spId}/mapping`, {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ product_id: exact.id }),
            });
          }
        });
      }
      updateCounters(workspace);
      window.alert(`${uniqueKeys.length} référence(s) associées à ${exact.code}.`);
    });

    const dialog = workspace.querySelector("#map-create-dialog");
    const form = workspace.querySelector("#map-create-form");
    workspace.querySelector("#map-create-product")?.addEventListener("click", () => {
      dialog?.showModal();
    });
    form?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submitter = event.submitter;
      if (submitter?.value === "cancel") {
        dialog.close();
        return;
      }
      const err = form.querySelector("[data-create-error]");
      err.hidden = true;
      const fd = new FormData(form);
      const payload = {
        code: String(fd.get("code") || "").trim().toUpperCase(),
        name: String(fd.get("name") || "").trim(),
        category: String(fd.get("category") || "").trim(),
        subcategory: String(fd.get("subcategory") || "").trim() || null,
        reference_unit: String(fd.get("reference_unit") || "").trim(),
        description: String(fd.get("description") || "").trim() || null,
      };
      const res = await fetch("/api/products", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        err.textContent = formatDetail(body.detail || body);
        err.hidden = false;
        return;
      }
      dialog.close();
      form.reset();
      window.alert(`Product créé : ${body.code}. Il est immédiatement disponible dans la recherche.`);
    });

    const editDialog = workspace.querySelector("#map-edit-dialog");
    const editForm = workspace.querySelector("#map-edit-form");
    let editingSpId = null;

    function fillEditForm(row) {
      editingSpId = row.supplier_product_id;
      const meta = editForm.querySelector("[data-edit-meta]");
      const attrs = editForm.querySelector("[data-edit-product-attrs]");
      const anomaly = editForm.querySelector("[data-edit-anomaly]");
      const hint = editForm.querySelector("[data-edit-hint]");
      const priceEl = editForm.querySelector("[data-edit-price]");
      const taxCalc = editForm.querySelector("[data-edit-tax-calc]");
      const clearBtn = editForm.querySelector("[data-clear-override]");
      meta.textContent = `${row.supplier} · ${row.external_reference} · ${row.name || ""}`;
      const pUnit = row.product_reference_unit || "—";
      let attrText = row.product_id
        ? `Product : ${row.product_code || ""} · unité besoin : ${pUnit}`
        : "Aucun Product associé";
      if (row.product_attributes && typeof row.product_attributes === "object") {
        const bits = Object.entries(row.product_attributes)
          .map(([k, v]) => `${k}=${v}`)
          .join(" · ");
        if (bits) attrText += ` · ${bits}`;
      }
      attrs.textContent = attrText;
      anomaly.hidden = !row.unit_anomaly;
      anomaly.textContent = row.unit_anomaly
        ? "Unité incompatible — offre exclue du comparateur"
        : "";
      editForm.supplier_unit.value = row.supplier_unit || "";
      editForm.packaging_quantity.value = row.packaging_quantity || "1";
      editForm.reference_unit.value = row.reference_unit || row.product_reference_unit || "pièce";
      editForm.reference_quantity.value = row.reference_quantity || "1";
      editForm.price.value = row.price != null && row.price !== "" ? row.price : "";
      editForm.tax_basis.value = row.tax_basis === "TTC" ? "TTC" : "HT";
      editForm.vat_rate.value =
        row.vat_rate != null && row.vat_rate !== "" ? row.vat_rate : "";
      const rq = Number(row.reference_quantity || 1);
      const pq = Number(row.packaging_quantity || 1);
      hint.textContent = `${rq} ${row.reference_unit || "pièce"} / ${row.supplier_unit || "pack"}`;
      if (row.price != null && row.price !== "" && rq > 0) {
        const unitPrice = (Number(row.price) / rq).toFixed(2);
        priceEl.textContent = `Prix pack source : ${row.price} ${row.tax_basis || ""} · indicatif ${unitPrice} € / ${row.reference_unit || "pièce"}`;
      } else {
        priceEl.textContent = row.price != null ? `Prix source : ${row.price} ${row.tax_basis || ""}` : "Sans prix";
      }
      refreshTaxCalc(editForm);
      if (clearBtn) clearBtn.hidden = row.correction_source !== "manual";
      editForm.querySelector("[data-edit-error]").hidden = true;
    }

    function refreshTaxCalc(form) {
      const el = form.querySelector("[data-edit-tax-calc]");
      if (!el) return;
      const price = Number(String(form.price.value || "").replace(",", "."));
      const basis = form.tax_basis.value;
      const vatRaw = String(form.vat_rate.value || "").trim();
      if (!Number.isFinite(price) || price < 0 || !basis) {
        el.textContent = "";
        return;
      }
      if (!vatRaw) {
        el.textContent = "Conversion HT/TTC indisponible : taux de TVA inconnu.";
        return;
      }
      const vat = Number(vatRaw.replace(",", "."));
      if (!Number.isFinite(vat) || vat < 0 || vat > 100) {
        el.textContent = "Taux de TVA invalide.";
        return;
      }
      let equiv;
      let label;
      if (basis === "TTC") {
        equiv = price / (1 + vat / 100);
        label = "HT";
      } else {
        equiv = price * (1 + vat / 100);
        label = "TTC";
      }
      el.textContent = `Calcul informatif : ${equiv.toLocaleString("fr-FR", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })} € ${label}`;
    }

    editForm?.addEventListener("input", (event) => {
      if (["price", "tax_basis", "vat_rate"].includes(event.target?.name)) {
        refreshTaxCalc(editForm);
      }
    });
    editForm?.tax_basis?.addEventListener("change", () => refreshTaxCalc(editForm));

    workspace.addEventListener("click", (event) => {
      const btn = event.target.closest?.(".map-edit-conditioning");
      if (!btn) return;
      const spId = Number(btn.dataset.spId);
      const row = currentRows.find((r) => Number(r.supplier_product_id) === spId);
      if (!row || !editDialog) return;
      fillEditForm(row);
      editDialog.showModal();
    });

    editForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submitter = event.submitter;
      const err = editForm.querySelector("[data-edit-error]");
      err.hidden = true;
      if (submitter?.value === "cancel") {
        editDialog.close();
        return;
      }
      if (!editingSpId) return;
      if (submitter?.value === "clear-override") {
        const res = await fetch(
          `/api/supplier-products/${editingSpId}/conditioning-override`,
          { method: "DELETE" }
        );
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          err.textContent = formatDetail(body.detail || body);
          err.hidden = false;
          return;
        }
        editDialog.close();
        if (catalogContext?.catalog_id) await openCatalogMappings(catalogContext.catalog_id);
        return;
      }
      const fd = new FormData(editForm);
      const priceRaw = String(fd.get("price") || "").trim().replace(",", ".");
      const vatRaw = String(fd.get("vat_rate") || "").trim().replace(",", ".");
      const payload = {
        supplier_unit: String(fd.get("supplier_unit") || "").trim(),
        packaging_quantity: String(fd.get("packaging_quantity") || "").trim(),
        reference_unit: String(fd.get("reference_unit") || "").trim(),
        reference_quantity: String(fd.get("reference_quantity") || "").trim(),
        tax_basis: String(fd.get("tax_basis") || "HT"),
        price: priceRaw === "" ? null : priceRaw,
        vat_rate: vatRaw === "" ? null : vatRaw,
      };
      const res = await fetch(`/api/supplier-products/${editingSpId}/conditioning`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        err.textContent = formatDetail(body.detail || body);
        err.hidden = false;
        return;
      }
      editDialog.close();
      if (catalogContext?.catalog_id) await openCatalogMappings(catalogContext.catalog_id);
      else window.alert("Conditionnement enregistré (override manuel).");
    });
  }

  function renderErrors(errors) {
    if (!errors?.length) return "";
    const items = errors
      .map((e) => {
        if (typeof e === "string") return `<li>${esc(e)}</li>`;
        return `<li>${e.line ? `Ligne ${esc(e.line)} · ` : ""}${
          e.code ? `<code>${esc(e.code)}</code> — ` : ""
        }${esc(e.message || e)}</li>`;
      })
      .join("");
    return `<div class="admin-preview-errors" role="alert"><h3>Erreur de prévisualisation</h3><ul>${items}</ul></div>`;
  }

  function renderPreview(data) {
    mode = "preview";
    catalogContext = null;
    const errors = data.errors || [];
    if (data.valid === false || errors.length) {
      previewBox.hidden = false;
      previewBox.innerHTML = `${renderErrors(errors)}
        <p class="error">La prévisualisation a échoué.</p>`;
      return;
    }
    const infos = (data.infos || []).map((i) => `<li>${esc(i)}</li>`).join("");
    const warnings = (data.warnings || []).map((w) => `<li>${esc(w)}</li>`).join("");
    previewMappings = new Map();
    previewBox.hidden = false;
    previewBox.innerHTML = `
      <h3>Prévisualisation</h3>
      <ul class="admin-preview-stats">
        <li><strong>${esc(data.supplier_name)}</strong> · ${esc(data.filename)}</li>
        <li>Lignes : ${esc(data.rows)} · agences magasin : ${esc(data.agencies)}</li>
        <li>Avec prix : ${esc(data.rows_with_price)} · sans prix : ${esc(data.rows_without_price)}</li>
        <li>HT : ${esc(data.rows_ht)} · TTC : ${esc(data.rows_ttc)} · base : <strong>${esc(data.tax_basis || "—")}</strong></li>
        <li>Mappés : ${esc(data.mapped)} · non mappés : ${esc(data.unmapped)}</li>
      </ul>
      ${infos ? `<ul class="admin-preview-infos">${infos}</ul>` : ""}
      ${warnings ? `<ul class="admin-preview-warnings">${warnings}</ul>` : ""}
      <h4>Association des références</h4>
      ${renderMappingWorkspace(data.mapping_rows || [])}
    `;
    const workspace = previewBox.querySelector("[data-map-workspace]");
    if (workspace) bindWorkspace(workspace);
  }

  function collectPreviewMappingsPayload() {
    const payload = [];
    previewMappings.forEach((productId, key) => {
      const idx = key.indexOf("||");
      const supplier = key.slice(0, idx);
      const ref = key.slice(idx + 2);
      payload.push({ supplier, external_reference: ref, product_id: productId });
    });
    return payload;
  }

  async function runPreview() {
    const file = fileInput.files?.[0];
    if (!file) {
      previewBox.hidden = false;
      previewBox.innerHTML = "<p class='error'>Choisissez un fichier CSV.</p>";
      lastPreviewOk = false;
      commitBtn.disabled = true;
      return;
    }
    lastFile = file;
    const body = new FormData();
    body.append("file", file);
    previewBtn.disabled = true;
    previewBox.hidden = false;
    previewBox.innerHTML = "<p>Analyse en cours…</p>";
    try {
      const res = await fetch("/api/supplier-imports/preview", { method: "POST", body });
      const data = await res.json();
      if (!res.ok) throw new Error(formatDetail(data.detail || data));
      renderPreview(data);
      lastPreviewOk = data.valid === true && !(data.errors && data.errors.length);
      commitBtn.disabled = !lastPreviewOk;
    } catch (err) {
      previewBox.innerHTML = `<p class="error">${esc(err.message || err)}</p>`;
      lastPreviewOk = false;
      commitBtn.disabled = true;
    } finally {
      previewBtn.disabled = false;
    }
  }

  async function runCommit() {
    const file = fileInput.files?.[0] || lastFile;
    if (!file || !lastPreviewOk) return;
    const body = new FormData();
    body.append("file", file);
    body.append("mappings", JSON.stringify(collectPreviewMappingsPayload()));
    commitBtn.disabled = true;
    previewBox.innerHTML = "<p>Import en cours…</p>";
    try {
      const res = await fetch("/api/supplier-imports", { method: "POST", body });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(formatDetail(data.detail || data));
      previewBox.innerHTML = `<p class="ok">Catalogue importé (id ${esc(data.catalog_id)}).</p>
        <p><a href="/admin/fournisseurs">Recharger</a></p>`;
    } catch (err) {
      previewBox.innerHTML = `<p class="error">${esc(err.message)}</p>`;
      commitBtn.disabled = false;
    }
  }

  async function openCatalogMappings(catalogId) {
    mode = "catalog";
    mappingSection.hidden = false;
    mappingPanel.innerHTML = "<p>Chargement…</p>";
    const res = await fetch(`/api/supplier-catalogs/${catalogId}/mappings`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      mappingPanel.innerHTML = `<p class="error">${esc(formatDetail(data.detail || data))}</p>`;
      return;
    }
    catalogContext = data;
    mappingMeta.textContent = `${data.filename} · ${data.source_key}`;
    previewMappings = new Map();
    const rows = (data.items || []).map((item) => ({
      supplier: item.supplier,
      external_reference: item.external_reference,
      name: item.name,
      brand: item.brand,
      supplier_unit: item.supplier_unit,
      packaging_quantity: item.packaging_quantity,
      reference_unit: item.reference_unit,
      reference_quantity: item.reference_quantity,
      price: item.price,
      tax_basis: item.tax_basis,
      vat_rate: item.vat_rate,
      image_url: item.image_url,
      product_id: item.product_id,
      product_code: item.product_code,
      product_name: item.product_name,
      product_reference_unit: item.product_reference_unit,
      product_attributes: item.product_attributes,
      unit_compatible: item.unit_compatible,
      unit_anomaly: item.unit_anomaly,
      correction_source: item.correction_source,
      supplier_product_id: item.supplier_product_id,
    }));
    mappingPanel.innerHTML = renderMappingWorkspace(rows);
    const workspace = mappingPanel.querySelector("[data-map-workspace]");
    if (workspace) bindWorkspace(workspace);
    mappingSection.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function catalogAction(url, method, okMessage) {
    const res = await fetch(url, { method });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(formatDetail(data.detail || data));
    window.alert(okMessage || "OK");
    window.location.reload();
  }

  previewBtn?.addEventListener("click", runPreview);
  commitBtn?.addEventListener("click", runCommit);
  fileInput?.addEventListener("change", () => {
    lastPreviewOk = false;
    commitBtn.disabled = true;
    previewMappings = new Map();
  });

  root.addEventListener("click", async (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const id = target.dataset.id;
    if (!id) return;
    try {
      if (target.classList.contains("catalog-map")) await openCatalogMappings(id);
      else if (target.classList.contains("catalog-activate"))
        await catalogAction(`/api/supplier-catalogs/${id}/activate`, "POST", "Catalogue activé.");
      else if (target.classList.contains("catalog-deactivate"))
        await catalogAction(`/api/supplier-catalogs/${id}/deactivate`, "POST", "Catalogue désactivé.");
      else if (target.classList.contains("catalog-delete")) {
        const key = target.dataset.key || id;
        if (!window.confirm(`Supprimer le catalogue « ${key} » ?`)) return;
        if (!window.confirm("Confirmation finale ?")) return;
        await catalogAction(`/api/supplier-catalogs/${id}?confirm=true`, "DELETE", "Supprimé.");
      }
    } catch (err) {
      window.alert(err.message || "Erreur");
    }
  });
})();
