(() => {
  const root = document.querySelector("[data-admin-catalogue]");
  if (!root) return;

  const PLACEHOLDER = "/static/product-placeholder.svg";
  const ATTR_LABELS = {
    type: "Type",
    length_mm: "Longueur (mm)",
    width_mm: "Largeur (mm)",
    thickness_mm: "Épaisseur (mm)",
    diameter_mm: "Diamètre (mm)",
    surface_m2: "Surface (m²)",
    profile: "Profil",
    finish: "Finition",
    lambda: "Lambda",
    R: "R",
    material: "Matériau",
  };

  const listView = document.getElementById("catalogue-list-view");
  const detailView = document.getElementById("catalogue-detail-view");
  const detailBox = document.getElementById("catalogue-detail");
  const tbody = document.getElementById("catalogue-tbody");
  const cards = document.getElementById("catalogue-cards");
  const meta = document.getElementById("catalogue-meta");
  const pagination = document.getElementById("catalogue-pagination");
  const categorySelect = document.getElementById("catalogue-category");

  let state = {
    page: 1,
    pageSize: 25,
    q: "",
    category: "",
    legacy: "exclude",
    active: "active",
    mapping: "all",
    anomaly: "all",
    has_price: "all",
  };
  let unmappedState = { page: 1, pageSize: 25, q: "" };
  let searchTimer = null;
  let currentProduct = null;

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function money(value) {
    if (value == null || value === "") return "—";
    const n = Number(String(value).replace(",", "."));
    if (!Number.isFinite(n)) return esc(value);
    return (
      n.toLocaleString("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) +
      "\u00a0€"
    );
  }

  function thumb(url, name) {
    const src = url ? esc(url) : PLACEHOLDER;
    return `<img class="product-thumb" src="${src}" alt="${esc(name || "")}" width="48" height="48" loading="lazy" decoding="async" onerror="this.onerror=null;this.src='${PLACEHOLDER}'" />`;
  }

  function flagsHtml(item) {
    const bits = [];
    if (item.is_legacy) bits.push('<span class="chip chip-warn">Legacy</span>');
    if (!item.is_active) bits.push('<span class="chip chip-muted">Inactif</span>');
    if (item.anomaly_count > 0)
      bits.push(`<span class="chip chip-danger">Anomalie ×${esc(item.anomaly_count)}</span>`);
    if ((item.supplier_product_count || 0) === 0) {
      bits.push('<span class="chip chip-muted">Sans référence fournisseur</span>');
    } else if (!item.has_price) {
      bits.push('<span class="chip chip-muted">Sans offre</span>');
    }
    return bits.join(" ") || "—";
  }

  function queryString(obj) {
    const params = new URLSearchParams();
    Object.entries(obj).forEach(([k, v]) => {
      if (v != null && v !== "") params.set(k, String(v));
    });
    return params.toString();
  }

  async function loadList() {
    const qs = queryString({
      q: state.q,
      category: state.category || undefined,
      legacy: state.legacy,
      active: state.active,
      mapping: state.mapping,
      anomaly: state.anomaly,
      has_price: state.has_price,
      page: state.page,
      page_size: state.pageSize,
    });
    const res = await fetch(`/api/admin/catalogue/products?${qs}`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      tbody.innerHTML = `<tr><td colspan="9" class="error">${esc(data.detail || "Erreur")}</td></tr>`;
      cards.innerHTML = "";
      return;
    }
    if (categorySelect && categorySelect.options.length <= 1) {
      (data.categories || []).forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c;
        opt.textContent = c;
        categorySelect.appendChild(opt);
      });
    }
    meta.textContent = `${data.total} produit(s) · page ${data.page}/${data.pages} · non rattachées : ${data.unmapped_count} · anomalies (filtre) : ${data.anomaly_product_count} · legacy DB : ${data.legacy_count}`;
    tbody.innerHTML = (data.items || [])
      .map(
        (item) => `<tr class="catalogue-row" data-id="${esc(item.id)}" tabindex="0">
        <td>${thumb(item.image_url, item.name)}</td>
        <td><strong>${esc(item.code)}</strong><div>${esc(item.name)}</div></td>
        <td>${esc(item.category)}${item.subcategory ? `<div class="muted">${esc(item.subcategory)}</div>` : ""}</td>
        <td>${esc(item.reference_unit)}</td>
        <td>${money(item.min_price_ht)}${item.min_price_ht != null ? " HT" : ""}</td>
        <td>${money(item.min_price_ttc)}${item.min_price_ttc != null ? " TTC" : ""}</td>
        <td>${esc(item.supplier_product_count)}</td>
        <td>${esc(item.offer_count)}</td>
        <td>${flagsHtml(item)}</td>
      </tr>`
      )
      .join("");
    cards.innerHTML = (data.items || [])
      .map(
        (item) => `<article class="catalogue-card" data-id="${esc(item.id)}">
        <div class="catalogue-card-media">${thumb(item.image_url, item.name)}</div>
        <div>
          <strong>${esc(item.code)}</strong>
          <div>${esc(item.name)}</div>
          <div class="muted">${esc(item.category)} · ${esc(item.reference_unit)}</div>
          <div>Min ${money(item.min_price_ht)} HT · ${money(item.min_price_ttc)} TTC</div>
          <div>${flagsHtml(item)}</div>
        </div>
      </article>`
      )
      .join("");
    renderPagination(pagination, data.page, data.pages, (p) => {
      state.page = p;
      loadList();
    });
  }

  function renderPagination(el, page, pages, onPage) {
    if (!el) return;
    if (pages <= 1) {
      el.innerHTML = "";
      return;
    }
    el.innerHTML = `
      <button type="button" class="button secondary" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>Préc.</button>
      <span class="muted">Page ${page} / ${pages}</span>
      <button type="button" class="button secondary" data-page="${page + 1}" ${page >= pages ? "disabled" : ""}>Suiv.</button>
    `;
    el.querySelectorAll("button[data-page]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const p = Number(btn.dataset.page);
        if (p >= 1 && p <= pages) onPage(p);
      });
    });
  }

  async function loadUnmapped() {
    const qs = queryString({
      q: unmappedState.q,
      page: unmappedState.page,
      page_size: unmappedState.pageSize,
    });
    const res = await fetch(`/api/admin/catalogue/unmapped?${qs}`);
    const data = await res.json().catch(() => ({}));
    const tb = document.getElementById("unmapped-tbody");
    const cardBox = document.getElementById("unmapped-cards");
    const pag = document.getElementById("unmapped-pagination");
    if (!res.ok) {
      tb.innerHTML = `<tr><td colspan="6" class="error">${esc(data.detail || "Erreur")}</td></tr>`;
      return;
    }
    const rows = data.items || [];
    tb.innerHTML = rows
      .map(
        (item) => `<tr>
        <td>${esc(item.supplier)}</td>
        <td><code>${esc(item.supplier_reference)}</code></td>
        <td>${esc(item.designation)}</td>
        <td>${esc(item.packaging_quantity || "")} ${esc(item.supplier_unit)} → ${esc(item.reference_quantity || "")} ${esc(item.reference_unit || "")}</td>
        <td>${item.price != null ? `${money(item.price)} ${esc(item.tax_basis || "")}` : "—"}</td>
        <td><a class="button secondary button-compact" href="/admin/fournisseurs">Rattacher</a></td>
      </tr>`
      )
      .join("");
    cardBox.innerHTML = rows
      .map(
        (item) => `<article class="catalogue-card">
        <div><strong>${esc(item.supplier)}</strong> · <code>${esc(item.supplier_reference)}</code></div>
        <div>${esc(item.designation)}</div>
        <div class="muted">${item.price != null ? `${money(item.price)} ${esc(item.tax_basis || "")}` : "Sans prix"}</div>
        <a class="button secondary button-compact" href="/admin/fournisseurs">Rattacher</a>
      </article>`
      )
      .join("");
    renderPagination(pag, data.page, data.pages, (p) => {
      unmappedState.page = p;
      loadUnmapped();
    });
  }

  function attrFieldsHtml(attrs) {
    const known = Object.keys(ATTR_LABELS);
    const rows = [];
    known.forEach((key) => {
      if (!(key in attrs)) return;
      rows.push(`<label>${esc(ATTR_LABELS[key])}
        <input name="attr_${esc(key)}" value="${esc(attrs[key])}" data-attr-key="${esc(key)}" /></label>`);
    });
    Object.keys(attrs).forEach((key) => {
      if (known.includes(key)) return;
      rows.push(`<label>${esc(key)}
        <input name="attr_${esc(key)}" value="${esc(attrs[key])}" data-attr-key="${esc(key)}" data-attr-custom="1" /></label>`);
    });
    if (!rows.length) {
      return '<p class="muted">Aucune caractéristique renseignée.</p>';
    }
    return `<div class="catalogue-attr-fields">${rows.join("")}</div>`;
  }

  function collectAttributes(form) {
    const attrs = {};
    form.querySelectorAll("[data-attr-key]").forEach((input) => {
      const key = input.dataset.attrKey;
      const raw = String(input.value ?? "").trim();
      if (raw === "") return;
      const asNum = Number(raw.replace(",", "."));
      attrs[key] = Number.isFinite(asNum) && /^-?\d+(\.\d+)?$/.test(raw.replace(",", "."))
        ? asNum
        : raw;
    });
    if (form.querySelector("[data-advanced-json]")?.checked) {
      const raw = String(form.attributes_json?.value || "").trim();
      if (raw) return JSON.parse(raw);
    }
    return Object.keys(attrs).length ? attrs : null;
  }

  async function openDetail(productId, pushUrl = true) {
    listView.hidden = true;
    detailView.hidden = false;
    detailBox.innerHTML = "<p>Chargement…</p>";
    if (pushUrl) history.pushState({ productId }, "", `/admin/catalogue/${productId}`);
    const res = await fetch(`/api/admin/catalogue/products/${productId}`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      detailBox.innerHTML = `<p class="error">${esc(data.detail || "Introuvable")}</p>`;
      return;
    }
    currentProduct = data;
    detailBox.innerHTML = renderDetail(data);
    bindDetail(data);
  }

  function renderDetail(p) {
    const attrs = p.attributes && typeof p.attributes === "object" ? p.attributes : {};
    const spBlocks = (p.supplier_products || [])
      .map((sp) => {
        const first = (sp.offers || [])[0];
        const priceLine =
          first != null
            ? `${money(first.price)} ${esc(first.tax_basis)}${
                first.vat_rate != null ? ` · TVA ${esc(first.vat_rate)} %` : ""
              }${first.price_ht != null ? ` · ${money(first.price_ht)} HT` : ""}${
                first.price_ttc != null ? ` · ${money(first.price_ttc)} TTC` : ""
              }`
            : "Sans offre";
        const offers = (sp.offers || [])
          .map(
            (o) => `<li>
              ${esc(o.agency_name)} · ${money(o.price)} ${esc(o.tax_basis)}
              ${o.vat_rate != null ? `· TVA ${esc(o.vat_rate)} %` : ""}
              ${o.price_ht != null ? `· ${money(o.price_ht)} HT` : ""}
              ${o.price_ttc != null ? `· ${money(o.price_ttc)} TTC` : ""}
              · stock ${esc(o.stock)}
            </li>`
          )
          .join("");
        return `<article class="catalogue-sp-card${sp.unit_anomaly ? " is-anomaly" : ""}" data-sp-id="${esc(sp.supplier_product_id)}">
          <header>
            <strong>${esc(sp.supplier)}</strong> · <code>${esc(sp.supplier_reference)}</code>
            ${sp.unit_anomaly ? '<span class="chip chip-danger">Unité incompatible</span>' : ""}
            ${sp.correction_source === "manual" ? '<span class="chip chip-warn">Correction manuelle</span>' : ""}
          </header>
          <p>${esc(sp.designation)}${sp.brand ? ` · ${esc(sp.brand)}` : ""}${sp.ean ? ` · EAN ${esc(sp.ean)}` : ""}</p>
          <p class="muted">
            Conditionnement : ${esc(sp.packaging_quantity)} ${esc(sp.supplier_unit)}
            → ${esc(sp.reference_quantity)} ${esc(sp.reference_unit || p.reference_unit)}
          </p>
          <p>Prix source : ${priceLine}</p>
          <p class="muted">
            Catalogue/source : ${esc(sp.catalog_id ?? "—")}
            ${sp.observed_at ? ` · observé ${esc(sp.observed_at)}` : ""}
            ${sp.source_url ? ` · <a href="${esc(sp.source_url)}" target="_blank" rel="noopener">source</a>` : ""}
          </p>
          <ul class="catalogue-offer-list">${offers || "<li class='muted'>Aucune offre</li>"}</ul>
          <div class="catalogue-sp-actions">
            <button type="button" class="button secondary button-compact sp-edit-conditioning" data-sp-id="${esc(sp.supplier_product_id)}">Modifier</button>
            <button type="button" class="button secondary button-compact sp-retarget" data-sp-id="${esc(sp.supplier_product_id)}">Changer le rattachement</button>
            <button type="button" class="button danger button-compact sp-detach" data-sp-id="${esc(sp.supplier_product_id)}">Détacher</button>
          </div>
        </article>`;
      })
      .join("");

    const emptyRefs = `<p class="muted">Aucune référence fournisseur rattachée.</p>`;

    return `
      <section class="admin-section catalogue-detail-head">
        <div class="catalogue-detail-media">${thumb(p.image_url, p.name)}</div>
        <div>
          <p class="muted">${esc(p.code)}${p.is_legacy ? " · Legacy" : ""}${p.is_active ? "" : " · Inactif"}</p>
          <h2>${esc(p.name)}</h2>
          <p>${esc(p.category)}${p.subcategory ? ` · ${esc(p.subcategory)}` : ""} · unité besoin <strong>${esc(p.reference_unit)}</strong></p>
          <p>Min ${money(p.min_price_ht)} HT · ${money(p.min_price_ttc)} TTC · ${esc(p.offer_count)} offre(s) · ${esc(p.anomaly_count)} anomalie(s)</p>
          ${p.description ? `<p class="muted">${esc(p.description)}</p>` : ""}
        </div>
      </section>

      <section class="admin-section">
        <h3>Édition Product</h3>
        <form class="catalogue-edit-form" id="catalogue-edit-form">
          <label>Nom <input name="name" required maxlength="200" value="${esc(p.name)}" /></label>
          <label>Catégorie <input name="category" required maxlength="80" value="${esc(p.category)}" /></label>
          <label>Sous-catégorie <input name="subcategory" maxlength="80" value="${esc(p.subcategory || "")}" /></label>
          <label>Description <textarea name="description" maxlength="500" rows="2">${esc(p.description || "")}</textarea></label>
          <fieldset class="catalogue-attrs-edit">
            <legend>Caractéristiques techniques</legend>
            ${attrFieldsHtml(attrs)}
          </fieldset>
          <details class="catalogue-advanced">
            <summary>Mode avancé (JSON)</summary>
            <label class="catalogue-check"><input type="checkbox" data-advanced-json /> Utiliser le JSON ci-dessous à l'enregistrement</label>
            <label>Attributs JSON
              <textarea name="attributes_json" rows="4" spellcheck="false">${esc(JSON.stringify(attrs, null, 2))}</textarea>
            </label>
          </details>
          <label class="catalogue-check"><input type="checkbox" name="is_active" ${p.is_active ? "checked" : ""} /> Actif</label>
          <label>Unité besoin
            <select name="reference_unit" ${p.can_edit_reference_unit ? "" : "disabled"}>
              ${(p.allowed_reference_units || []).map((u) => `<option value="${esc(u)}" ${u === p.reference_unit ? "selected" : ""}>${esc(u)}</option>`).join("")}
            </select>
          </label>
          ${
            p.can_edit_reference_unit
              ? ""
              : `<p class="muted">Unité verrouillée : utilisée dans ${esc(p.chantier_usage_count)} chantier(s).</p>`
          }
          <button type="submit" class="button">Enregistrer</button>
          <p class="error" data-edit-error hidden></p>
          <p class="ok" data-edit-ok hidden>Enregistré.</p>
        </form>
      </section>

      <section class="admin-section">
        <div class="catalogue-refs-head">
          <h3>Références fournisseurs (${esc((p.supplier_products || []).length)})</h3>
          <button type="button" class="button" id="attach-ref-btn">+ Rattacher une référence fournisseur</button>
        </div>
        <div class="catalogue-sp-list">${spBlocks || emptyRefs}</div>
      </section>

      <dialog class="map-edit-dialog catalogue-attach-dialog" id="catalogue-attach-dialog">
        <form method="dialog" class="map-edit-form" id="catalogue-attach-form">
          <h3>Rattacher une référence fournisseur</h3>
          <p class="muted">Produit cible : <strong>${esc(p.code)}</strong> — ${esc(p.name)}</p>
          <label>Rechercher une référence fournisseur…
            <input type="search" name="q" placeholder="Fournisseur, référence, désignation, marque, EAN…" maxlength="100" />
          </label>
          <div class="catalogue-attach-results" data-attach-results></div>
          <div class="map-create-actions">
            <button type="submit" class="button secondary" value="cancel">Fermer</button>
          </div>
          <p class="error" data-attach-error hidden></p>
        </form>
      </dialog>

      <dialog class="map-edit-dialog" id="catalogue-retarget-dialog">
        <form method="dialog" class="map-edit-form" id="catalogue-retarget-form">
          <h3>Changer le rattachement</h3>
          <p class="muted" data-retarget-meta></p>
          <label>Rechercher un produit ProMatConnect
            <input type="search" name="q" placeholder="Code ou nom…" maxlength="100" />
          </label>
          <div class="catalogue-attach-results" data-retarget-results></div>
          <div class="map-create-actions">
            <button type="submit" class="button secondary" value="cancel">Fermer</button>
          </div>
          <p class="error" data-retarget-error hidden></p>
        </form>
      </dialog>

      <dialog class="map-edit-dialog" id="catalogue-sp-dialog">
        <form method="dialog" class="map-edit-form" id="catalogue-sp-form">
          <h3>Modifier conditionnement / TVA</h3>
          <p class="muted" data-sp-meta></p>
          <label>Unité fournisseur <input name="supplier_unit" required maxlength="40" /></label>
          <label>Quantité par pack <input name="packaging_quantity" type="number" step="0.001" min="0.001" required /></label>
          <label>Unité besoin
            <select name="reference_unit" required>
              ${(p.allowed_reference_units || []).map((u) => `<option value="${esc(u)}">${esc(u)}</option>`).join("")}
            </select>
          </label>
          <label>Contenu unités besoin <input name="reference_quantity" type="number" step="0.001" min="0.001" required /></label>
          <fieldset class="map-edit-tax">
            <legend>Prix source / TVA</legend>
            <label>Prix source <input name="price" type="number" step="0.01" min="0" /></label>
            <label>Base
              <select name="tax_basis"><option value="HT">HT</option><option value="TTC">TTC</option></select>
            </label>
            <label>TVA <span class="map-edit-vat-row"><input name="vat_rate" type="number" step="0.01" min="0" max="100" /><span>%</span></span></label>
          </fieldset>
          <div class="map-create-actions">
            <button type="submit" class="button" value="save">Enregistrer</button>
            <button type="submit" class="button secondary" value="cancel">Annuler</button>
            <button type="submit" class="button secondary" value="clear-override" data-clear-override hidden>Lever l'override</button>
          </div>
          <p class="error" data-sp-error hidden></p>
        </form>
      </dialog>
    `;
  }

  async function putMapping(spId, productId) {
    const res = await fetch(`/api/supplier-products/${spId}/mapping`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product_id: productId }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(typeof body.detail === "string" ? body.detail : "Rattachement impossible.");
    }
    return body;
  }

  function bindDetail(product) {
    const form = detailBox.querySelector("#catalogue-edit-form");
    form?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const err = form.querySelector("[data-edit-error]");
      const ok = form.querySelector("[data-edit-ok]");
      err.hidden = true;
      ok.hidden = true;
      let attributes;
      try {
        attributes = collectAttributes(form);
      } catch {
        err.textContent = "JSON attributs invalide.";
        err.hidden = false;
        return;
      }
      const fd = new FormData(form);
      const payload = {
        name: String(fd.get("name") || "").trim(),
        category: String(fd.get("category") || "").trim(),
        subcategory: String(fd.get("subcategory") || "").trim() || null,
        description: String(fd.get("description") || "").trim() || null,
        attributes,
        is_active: form.is_active.checked,
      };
      const res = await fetch(`/api/admin/catalogue/products/${product.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        err.textContent = typeof body.detail === "string" ? body.detail : "Enregistrement impossible.";
        err.hidden = false;
        return;
      }
      const unitSelect = form.reference_unit;
      if (product.can_edit_reference_unit && unitSelect && unitSelect.value !== product.reference_unit) {
        const uRes = await fetch(`/api/products/${product.id}/reference-unit`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reference_unit: unitSelect.value }),
        });
        if (!uRes.ok) {
          const uBody = await uRes.json().catch(() => ({}));
          err.textContent =
            uBody.detail?.message || uBody.detail || "Unité besoin non modifiable.";
          err.hidden = false;
          return;
        }
      }
      ok.hidden = false;
      openDetail(product.id, false);
    });

    // Attach unmapped reference
    const attachDialog = detailBox.querySelector("#catalogue-attach-dialog");
    const attachForm = detailBox.querySelector("#catalogue-attach-form");
    const attachResults = attachForm?.querySelector("[data-attach-results]");
    let attachTimer = null;

    async function searchUnmapped(q) {
      const qs = queryString({ q, page: 1, page_size: 20 });
      const res = await fetch(`/api/admin/catalogue/unmapped?${qs}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        attachResults.innerHTML = `<p class="error">${esc(data.detail || "Erreur")}</p>`;
        return;
      }
      const items = data.items || [];
      if (!items.length) {
        attachResults.innerHTML = '<p class="muted">Aucune référence non rattachée trouvée.</p>';
        return;
      }
      attachResults.innerHTML = items
        .map((item) => {
          const price =
            item.price != null
              ? `${money(item.price)} ${esc(item.tax_basis || "")}${
                  item.price_ht != null && item.tax_basis === "TTC"
                    ? ` · ${money(item.price_ht)} HT`
                    : ""
                }`
              : "Sans prix";
          return `<article class="catalogue-attach-item">
            <div>
              <strong>${esc(item.supplier)}</strong> · <code>${esc(item.supplier_reference)}</code>
              <div>${esc(item.designation)}</div>
              <div class="muted">${item.brand ? esc(item.brand) + " · " : ""}${price}</div>
            </div>
            <button type="button" class="button button-compact attach-confirm"
              data-sp-id="${esc(item.supplier_product_id)}"
              data-supplier="${esc(item.supplier)}"
              data-ref="${esc(item.supplier_reference)}">Rattacher</button>
          </article>`;
        })
        .join("");
      attachResults.querySelectorAll(".attach-confirm").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const spId = Number(btn.dataset.spId);
          const ok = window.confirm(
            `Rattacher la référence ${btn.dataset.supplier} ${btn.dataset.ref}\nà ${product.name} ?`
          );
          if (!ok) return;
          try {
            await putMapping(spId, product.id);
            attachDialog.close();
            openDetail(product.id, false);
          } catch (e) {
            const errEl = attachForm.querySelector("[data-attach-error]");
            errEl.textContent = e.message || "Erreur";
            errEl.hidden = false;
          }
        });
      });
    }

    detailBox.querySelector("#attach-ref-btn")?.addEventListener("click", () => {
      attachForm.q.value = "";
      attachResults.innerHTML = '<p class="muted">Saisissez une recherche pour lister les références non rattachées.</p>';
      attachForm.querySelector("[data-attach-error]").hidden = true;
      attachDialog.showModal();
      searchUnmapped("");
    });
    attachForm?.q?.addEventListener("input", () => {
      clearTimeout(attachTimer);
      attachTimer = setTimeout(() => searchUnmapped(attachForm.q.value.trim()), 250);
    });
    attachForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      if (event.submitter?.value === "cancel") attachDialog.close();
    });

    // Detach
    detailBox.querySelectorAll(".sp-detach").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const spId = Number(btn.dataset.spId);
        const sp = (product.supplier_products || []).find((s) => s.supplier_product_id === spId);
        if (!sp) return;
        const ok = window.confirm(
          `Détacher la référence ${sp.supplier} ${sp.supplier_reference} de ${product.name} ?\n` +
            `La référence et ses offres seront conservées, sans produit ProMatConnect.`
        );
        if (!ok) return;
        try {
          await putMapping(spId, null);
          openDetail(product.id, false);
        } catch (e) {
          window.alert(e.message || "Détachement impossible.");
        }
      });
    });

    // Retarget to another Product
    const retargetDialog = detailBox.querySelector("#catalogue-retarget-dialog");
    const retargetForm = detailBox.querySelector("#catalogue-retarget-form");
    const retargetResults = retargetForm?.querySelector("[data-retarget-results]");
    let retargetSp = null;
    let retargetTimer = null;

    async function searchProducts(q) {
      const qs = queryString({ q, limit: 20, for_mapping: true });
      const res = await fetch(`/api/products?${qs}`);
      const items = await res.json().catch(() => []);
      if (!res.ok || !Array.isArray(items)) {
        retargetResults.innerHTML = '<p class="error">Recherche impossible.</p>';
        return;
      }
      const filtered = items.filter((it) => it.id !== product.id);
      if (!filtered.length) {
        retargetResults.innerHTML = '<p class="muted">Aucun autre produit trouvé.</p>';
        return;
      }
      retargetResults.innerHTML = filtered
        .map(
          (it) => `<article class="catalogue-attach-item">
            <div><strong>${esc(it.code)}</strong><div>${esc(it.name)}</div></div>
            <button type="button" class="button button-compact retarget-confirm"
              data-product-id="${esc(it.id)}"
              data-code="${esc(it.code)}"
              data-name="${esc(it.name)}">Choisir</button>
          </article>`
        )
        .join("");
      retargetResults.querySelectorAll(".retarget-confirm").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const targetId = Number(btn.dataset.productId);
          const ok = window.confirm(
            `Changer le rattachement de ${retargetSp.supplier} ${retargetSp.supplier_reference}\n` +
              `vers ${btn.dataset.code} — ${btn.dataset.name} ?`
          );
          if (!ok) return;
          try {
            await putMapping(retargetSp.supplier_product_id, targetId);
            retargetDialog.close();
            openDetail(product.id, false);
          } catch (e) {
            const errEl = retargetForm.querySelector("[data-retarget-error]");
            errEl.textContent = e.message || "Erreur";
            errEl.hidden = false;
          }
        });
      });
    }

    detailBox.querySelectorAll(".sp-retarget").forEach((btn) => {
      btn.addEventListener("click", () => {
        const spId = Number(btn.dataset.spId);
        retargetSp = (product.supplier_products || []).find((s) => s.supplier_product_id === spId);
        if (!retargetSp) return;
        retargetForm.querySelector("[data-retarget-meta]").textContent =
          `${retargetSp.supplier} · ${retargetSp.supplier_reference} · actuellement sur ${product.code}`;
        retargetForm.q.value = "";
        retargetResults.innerHTML = "";
        retargetForm.querySelector("[data-retarget-error]").hidden = true;
        retargetDialog.showModal();
        searchProducts("");
      });
    });
    retargetForm?.q?.addEventListener("input", () => {
      clearTimeout(retargetTimer);
      retargetTimer = setTimeout(() => searchProducts(retargetForm.q.value.trim()), 250);
    });
    retargetForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      if (event.submitter?.value === "cancel") retargetDialog.close();
    });

    // Conditioning editor (reuse existing API)
    const dialog = detailBox.querySelector("#catalogue-sp-dialog");
    const spForm = detailBox.querySelector("#catalogue-sp-form");
    let editingSp = null;

    detailBox.querySelectorAll(".sp-edit-conditioning").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = Number(btn.dataset.spId);
        editingSp = (product.supplier_products || []).find((s) => s.supplier_product_id === id);
        if (!editingSp || !dialog) return;
        spForm.querySelector("[data-sp-meta]").textContent =
          `${editingSp.supplier} · ${editingSp.supplier_reference}`;
        spForm.supplier_unit.value = editingSp.supplier_unit || "";
        spForm.packaging_quantity.value = editingSp.packaging_quantity || "1";
        spForm.reference_unit.value = editingSp.reference_unit || product.reference_unit;
        spForm.reference_quantity.value = editingSp.reference_quantity || "1";
        const firstOffer = (editingSp.offers || [])[0];
        spForm.price.value = firstOffer?.price ?? "";
        spForm.tax_basis.value = firstOffer?.tax_basis === "TTC" ? "TTC" : "HT";
        spForm.vat_rate.value = firstOffer?.vat_rate ?? "";
        const clearBtn = spForm.querySelector("[data-clear-override]");
        if (clearBtn) clearBtn.hidden = editingSp.correction_source !== "manual";
        spForm.querySelector("[data-sp-error]").hidden = true;
        dialog.showModal();
      });
    });

    spForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submitter = event.submitter;
      const err = spForm.querySelector("[data-sp-error]");
      err.hidden = true;
      if (submitter?.value === "cancel") {
        dialog.close();
        return;
      }
      if (!editingSp) return;
      if (submitter?.value === "clear-override") {
        const res = await fetch(
          `/api/supplier-products/${editingSp.supplier_product_id}/conditioning-override`,
          { method: "DELETE" }
        );
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          err.textContent = body.detail || "Impossible de lever l'override.";
          err.hidden = false;
          return;
        }
        dialog.close();
        openDetail(product.id, false);
        return;
      }
      const fd = new FormData(spForm);
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
      const res = await fetch(
        `/api/supplier-products/${editingSp.supplier_product_id}/conditioning`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        }
      );
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        err.textContent = typeof body.detail === "string" ? body.detail : "Enregistrement impossible.";
        err.hidden = false;
        return;
      }
      dialog.close();
      openDetail(product.id, false);
    });
  }

  function showList(pushUrl = true) {
    detailView.hidden = true;
    listView.hidden = false;
    currentProduct = null;
    if (pushUrl) history.pushState({}, "", "/admin/catalogue");
    loadList();
    loadUnmapped();
  }

  root.addEventListener("click", (event) => {
    const row = event.target.closest?.("[data-id].catalogue-row, [data-id].catalogue-card");
    if (row && !event.target.closest("a,button,input,select")) {
      openDetail(Number(row.dataset.id));
    }
  });
  root.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    const row = event.target.closest?.("tr.catalogue-row[data-id]");
    if (row) openDetail(Number(row.dataset.id));
  });

  document.getElementById("catalogue-back")?.addEventListener("click", (event) => {
    event.preventDefault();
    showList(true);
  });

  document.getElementById("catalogue-legacy")?.addEventListener("change", (e) => {
    state.legacy = e.target.value; state.page = 1; loadList();
  });
  document.getElementById("catalogue-active")?.addEventListener("change", (e) => {
    state.active = e.target.value; state.page = 1; loadList();
  });
  document.getElementById("catalogue-mapping")?.addEventListener("change", (e) => {
    state.mapping = e.target.value; state.page = 1; loadList();
  });
  document.getElementById("catalogue-anomaly")?.addEventListener("change", (e) => {
    state.anomaly = e.target.value; state.page = 1; loadList();
  });
  document.getElementById("catalogue-has-price")?.addEventListener("change", (e) => {
    state.has_price = e.target.value; state.page = 1; loadList();
  });
  document.getElementById("catalogue-category")?.addEventListener("change", (e) => {
    state.category = e.target.value; state.page = 1; loadList();
  });

  document.getElementById("catalogue-q")?.addEventListener("input", (event) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.q = event.target.value.trim();
      state.page = 1;
      loadList();
    }, 250);
  });

  document.getElementById("unmapped-q")?.addEventListener("input", (event) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      unmappedState.q = event.target.value.trim();
      unmappedState.page = 1;
      loadUnmapped();
    }, 250);
  });

  window.addEventListener("popstate", () => {
    const match = location.pathname.match(/\/admin\/catalogue\/(\d+)/);
    if (match) openDetail(Number(match[1]), false);
    else showList(false);
  });

  const initialId = root.dataset.productId ? Number(root.dataset.productId) : null;
  if (initialId) openDetail(initialId, false);
  else {
    loadList();
    loadUnmapped();
  }
})();
