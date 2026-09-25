(() => {
  const form = document.getElementById("supplier-import-form");
  if (!form) return;

  const fileInput = document.getElementById("supplier-csv");
  const previewBtn = document.getElementById("supplier-preview-btn");
  const commitBtn = document.getElementById("supplier-commit-btn");
  const previewBox = document.getElementById("supplier-preview");
  let lastFile = null;
  let previewValid = false;

  function renderPreview(data) {
    previewBox.hidden = false;
    const errors = data.errors || [];
    const warnings = data.warnings || [];
    const unmapped = data.unmapped_products || [];
    const suppliers = (data.suppliers || []).join(", ") || "—";
    previewValid = !!data.valid && errors.length === 0;
    commitBtn.disabled = !previewValid;

    const errHtml = errors.length
      ? `<ul class="admin-issues is-error">${errors
          .map((e) => `<li>L.${e.line} [${e.code}] ${e.message}</li>`)
          .join("")}</ul>`
      : `<p class="ok">Aucune erreur bloquante.</p>`;
    const warnHtml = warnings.length
      ? `<ul class="admin-issues is-warn">${warnings
          .map((w) => `<li>L.${w.line} [${w.code}] ${w.message}</li>`)
          .join("")}</ul>`
      : "";
    const unmappedHtml = unmapped.length
      ? `<table class="admin-table"><thead><tr><th>Référence</th><th>Désignation</th><th>Fournisseur</th><th>Statut</th></tr></thead><tbody>${unmapped
          .map(
            (u) =>
              `<tr><td>${escapeHtml(u.external_reference)}</td><td>${escapeHtml(
                u.name
              )}</td><td>${escapeHtml(u.supplier)}</td><td>Non associé</td></tr>`
          )
          .join("")}</tbody></table>
          <p class="muted">Action future : Associer à un produit (non implémentée).</p>`
      : `<p class="muted">Tous les produits sont mappés.</p>`;

    previewBox.innerHTML = `
      <h3>Rapport de prévisualisation</h3>
      <dl class="admin-stats">
        <div><dt>Fournisseur(s)</dt><dd>${escapeHtml(suppliers)}</dd></div>
        <div><dt>Lignes</dt><dd>${data.rows ?? 0}</dd></div>
        <div><dt>Agences</dt><dd>${data.agencies ?? 0}</dd></div>
        <div><dt>Références</dt><dd>${data.supplier_references ?? 0}</dd></div>
        <div><dt>Offres</dt><dd>${data.offers ?? 0}</dd></div>
        <div><dt>Mappées</dt><dd>${data.mapped ?? 0}</dd></div>
        <div><dt>Non mappées</dt><dd>${data.unmapped ?? 0}</dd></div>
        <div><dt>Erreurs</dt><dd>${errors.length}</dd></div>
        <div><dt>Warnings</dt><dd>${warnings.length}</dd></div>
      </dl>
      ${errHtml}
      ${warnHtml}
      <h4>Produits non mappés</h4>
      ${unmappedHtml}
    `;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function postFile(url) {
    const file = fileInput.files && fileInput.files[0];
    if (!file) {
      previewBox.hidden = false;
      previewBox.innerHTML = `<p class="admin-issues is-error">Choisissez un fichier CSV.</p>`;
      commitBtn.disabled = true;
      return null;
    }
    lastFile = file;
    const body = new FormData();
    body.append("file", file, file.name);
    const response = await fetch(url, { method: "POST", body });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = payload.detail || payload;
      renderPreview(
        typeof detail === "object" && detail.errors
          ? detail
          : {
              valid: false,
              errors: [
                {
                  line: 0,
                  code: "http",
                  message: typeof detail === "string" ? detail : JSON.stringify(detail),
                },
              ],
            }
      );
      return null;
    }
    return payload;
  }

  previewBtn.addEventListener("click", async () => {
    previewBtn.disabled = true;
    try {
      const data = await postFile("/api/supplier-imports/preview");
      if (data) renderPreview(data);
    } finally {
      previewBtn.disabled = false;
    }
  });

  commitBtn.addEventListener("click", async () => {
    if (!previewValid || !fileInput.files?.[0]) return;
    commitBtn.disabled = true;
    try {
      const data = await postFile("/api/supplier-imports");
      if (data && data.valid) {
        previewBox.hidden = false;
        previewBox.innerHTML = `<p class="ok">Import confirmé · source_key=${escapeHtml(
          data.source_key
        )} · ${data.offers} offres · mappées ${data.mapped} / non mappées ${data.unmapped}.</p>`;
        window.setTimeout(() => window.location.reload(), 800);
      }
    } finally {
      if (!previewValid) commitBtn.disabled = true;
    }
  });

  fileInput.addEventListener("change", () => {
    previewValid = false;
    commitBtn.disabled = true;
    previewBox.hidden = true;
    previewBox.innerHTML = "";
  });
})();
