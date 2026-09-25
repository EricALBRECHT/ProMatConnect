"use strict";

const {
  $,
  node,
  fetchProducts,
  bindMaterialLines,
  apiErrorMessage,
  reportQuantityFields,
} = window.ProMatMaterials;

const CONFLICT_MESSAGE =
  "Ce chantier a été modifié dans un autre onglet. Rechargez la page avant de poursuivre.";
const STALE_APPRO_MESSAGE =
  "Les besoins du chantier ont changé depuis cette comparaison.";

const APPRO_STATUS_LABELS = {
  none: "À comparer",
  retained: "Approvisionnement retenu",
  obsolete: "Comparaison à refaire",
};

const money = (value) =>
  new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR" }).format(
    Number(value),
  );
const number = (value) =>
  new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 3 }).format(
    Number(value),
  );

function status(message = "", error = false) {
  const el = $("status");
  if (!el) return;
  el.textContent = message;
  el.className = error ? "status error" : "status";
}

function formatDate(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat("fr-FR", {
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(new Date(`${value}T00:00:00`));
}

function optionalText(value) {
  const trimmed = (value || "").trim();
  return trimmed || null;
}

function optionalCoord(value) {
  const trimmed = (value || "").trim();
  return trimmed === "" ? null : trimmed;
}

function readFormMeta() {
  return {
    nom: $("nom").value.trim(),
    client: optionalText($("client").value),
    adresse: $("adresse").value.trim(),
    date_prevue: optionalText($("date_prevue").value),
    notes: optionalText($("notes").value),
    latitude: optionalCoord($("latitude").value),
    longitude: optionalCoord($("longitude").value),
  };
}

function fillForm(chantier) {
  $("nom").value = chantier.nom || "";
  $("client").value = chantier.client || "";
  $("adresse").value = chantier.adresse || "";
  $("date_prevue").value = chantier.date_prevue || "";
  $("notes").value = chantier.notes || "";
  $("latitude").value = chantier.latitude ?? "";
  $("longitude").value = chantier.longitude ?? "";
  if ($("updated_at")) $("updated_at").value = chantier.updated_at || "";
  if ($("detail-title")) $("detail-title").textContent = chantier.nom;
  renderIdentityCompact(chantier);
}

function renderIdentityCompact(chantier) {
  const clientEl = $("identity-compact-client");
  const adresseEl = $("identity-compact-adresse");
  const dateEl = $("identity-compact-date");
  const notesEl = $("identity-compact-notes");
  if (!clientEl || !adresseEl || !dateEl) return;
  const client = (chantier.client || "").trim();
  clientEl.textContent = client ? `Client : ${client}` : "Client : —";
  adresseEl.textContent = chantier.adresse || "";
  dateEl.textContent = chantier.date_prevue
    ? `Prévu le ${formatDate(chantier.date_prevue)}`
    : "";
  dateEl.hidden = !chantier.date_prevue;
  const notes = (chantier.notes || "").trim();
  if (notesEl) {
    notesEl.textContent = notes;
    notesEl.hidden = !notes;
  }
}

async function loadList() {
  const grid = $("chantiers-grid");
  const empty = $("chantiers-empty");
  const none = $("chantiers-none");
  const toolbar = $("chantiers-toolbar");
  const searchInput = $("chantier-search");
  const sortSelect = $("chantier-sort");
  let chantiers = [];
  const params = new URLSearchParams(window.location.search);
  const justDeleted = params.get("supprime") === "1";
  if (justDeleted) {
    status("Chantier supprimé.");
    params.delete("supprime");
    const next = params.toString();
    window.history.replaceState({}, "", next ? `/chantiers?${next}` : "/chantiers");
  } else {
    status("Chargement…");
  }

  function matchesSearch(chantier, needle) {
    if (!needle) return true;
    const haystack = [chantier.nom, chantier.client || "", chantier.adresse]
      .join(" ")
      .toLocaleLowerCase("fr");
    return haystack.includes(needle);
  }

  function sortedChantiers(items) {
    const mode = (sortSelect && sortSelect.value) || "recent";
    const copy = items.slice();
    if (mode === "name") {
      copy.sort((a, b) =>
        a.nom.localeCompare(b.nom, "fr", { sensitivity: "base" }),
      );
    } else if (mode === "date") {
      copy.sort((a, b) => {
        if (!a.date_prevue && !b.date_prevue) return b.id - a.id;
        if (!a.date_prevue) return 1;
        if (!b.date_prevue) return -1;
        return a.date_prevue.localeCompare(b.date_prevue) || b.id - a.id;
      });
    } else {
      copy.sort((a, b) => {
        const byDate = String(b.updated_at).localeCompare(String(a.updated_at));
        return byDate || b.id - a.id;
      });
    }
    return copy;
  }

  function statusBadge(statusKey) {
    const badge = node("span", APPRO_STATUS_LABELS[statusKey] || statusKey, `badge status-${statusKey}`);
    if (statusKey === "retained") {
      badge.prepend(document.createTextNode("✓ "));
    } else if (statusKey === "obsolete") {
      badge.prepend(document.createTextNode("⚠ "));
    }
    return badge;
  }

  function renderCard(chantier) {
    const card = node("a", undefined, "chantier-card");
    card.href = `/chantiers/${chantier.id}`;
    card.setAttribute("role", "listitem");
    card.setAttribute("aria-label", `Ouvrir le chantier ${chantier.nom}`);

    const body = node("div", undefined, "chantier-card-body");
    body.append(node("h2", chantier.nom));
    if (chantier.client) {
      body.append(node("p", `Client : ${chantier.client}`, "chantier-client"));
    }
    body.append(node("p", chantier.adresse, "chantier-address"));
    if (chantier.date_prevue) {
      body.append(
        node("p", `Prévu le ${formatDate(chantier.date_prevue)}`, "chantier-date"),
      );
    }
    const count = chantier.materiaux_count;
    body.append(
      node(
        "p",
        `${count} matériau${count > 1 ? "x" : ""}`,
        "chantier-count",
      ),
    );
    const statusKey = chantier.approvisionnement_status || "none";
    body.append(statusBadge(statusKey));
    if (chantier.material_total != null && chantier.material_total !== "") {
      body.append(
        node("p", `${money(chantier.material_total)} HT`, "chantier-total"),
      );
    }
    const open = node("span", "Ouvrir", "button secondary chantier-open");
    open.setAttribute("aria-hidden", "true");
    card.append(body, open);
    return card;
  }

  function render() {
    const needle = (searchInput?.value || "").trim().toLocaleLowerCase("fr");
    const filtered = sortedChantiers(
      chantiers.filter((item) => matchesSearch(item, needle)),
    );
    if (!chantiers.length) {
      toolbar.hidden = true;
      grid.hidden = true;
      grid.replaceChildren();
      empty.hidden = false;
      none.hidden = true;
      return;
    }
    empty.hidden = true;
    toolbar.hidden = false;
    if (!filtered.length) {
      grid.hidden = true;
      grid.replaceChildren();
      none.hidden = false;
      return;
    }
    none.hidden = true;
    grid.hidden = false;
    grid.replaceChildren(...filtered.map(renderCard));
  }

  try {
    const response = await fetch("/api/chantiers");
    if (!response.ok) throw new Error("Impossible de charger les chantiers.");
    chantiers = await response.json();
    render();
    if (searchInput) searchInput.addEventListener("input", render);
    if (sortSelect) sortSelect.addEventListener("change", render);
    if (!justDeleted) status();
  } catch (error) {
    empty.hidden = true;
    none.hidden = true;
    if (toolbar) toolbar.hidden = true;
    grid.hidden = true;
    status(error.message || "Connexion impossible. Réessayez.", true);
  }
}

async function setupCreate() {
  const form = $("chantier-form");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const button = $("create-button");
    button.disabled = true;
    status("Création du chantier…");
    try {
      const response = await fetch("/api/chantiers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...readFormMeta(), materiaux: [] }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          apiErrorMessage(payload, "Impossible de créer ce chantier."),
        );
      }
      window.location.assign(`/chantiers/${payload.id}`);
    } catch (error) {
      status(error.message || "Connexion impossible. Réessayez.", true);
      button.disabled = false;
    }
  });
}

async function setupDetail(chantierId) {
  let products = [];
  let lines = [];
  let dirty = false;
  let saving = false;
  let chantierNom = "";
  let hasApprovisionnement = false;
  let identityEditing = false;
  let identitySnapshot = null;
  let currentChantier = null;

  function markDirty() {
    dirty = true;
    status();
  }

  const materialList = bindMaterialLines({
    getProducts: () => products,
    getLines: () => lines,
    setLines: (next) => {
      lines = next;
    },
    onChange: markDirty,
    status,
  });

  function showIdentityCompact() {
    identityEditing = false;
    identitySnapshot = null;
    const compact = $("identity-compact");
    const panel = $("identity-edit-panel");
    if (compact) compact.hidden = false;
    if (panel) panel.hidden = true;
  }

  function showIdentityEdit() {
    identityEditing = true;
    identitySnapshot = {
      nom: $("nom").value,
      client: $("client").value,
      adresse: $("adresse").value,
      date_prevue: $("date_prevue").value,
      notes: $("notes").value,
      latitude: $("latitude").value,
      longitude: $("longitude").value,
    };
    const compact = $("identity-compact");
    const panel = $("identity-edit-panel");
    if (compact) compact.hidden = true;
    if (panel) panel.hidden = false;
    $("nom").focus();
  }

  function cancelIdentityEdit() {
    if (!identityEditing || !identitySnapshot) {
      showIdentityCompact();
      return;
    }
    $("nom").value = identitySnapshot.nom;
    $("client").value = identitySnapshot.client;
    $("adresse").value = identitySnapshot.adresse;
    $("date_prevue").value = identitySnapshot.date_prevue;
    $("notes").value = identitySnapshot.notes;
    $("latitude").value = identitySnapshot.latitude;
    $("longitude").value = identitySnapshot.longitude;
    if (currentChantier) {
      $("detail-title").textContent = currentChantier.nom;
      renderIdentityCompact(currentChantier);
    }
    showIdentityCompact();
    status();
  }

  function setApproBadge(statusKey) {
    const badge = $("appro-badge");
    if (!badge) return;
    const labels = {
      none: "À comparer",
      retained: "Solution retenue",
      obsolete: "Comparaison à refaire",
    };
    const label = labels[statusKey] || statusKey;
    badge.className = `badge status-${statusKey}`;
    badge.textContent =
      statusKey === "retained"
        ? `✓ ${label}`
        : statusKey === "obsolete"
          ? `⚠ ${label}`
          : label;
    // Badge section : uniquement l’état vide ; sinon proche du titre de stratégie.
    badge.hidden = statusKey !== "none";
  }

  function strategyStatusBadge(statusKey) {
    const labels = {
      retained: "✓ Solution retenue",
      obsolete: "⚠ Comparaison à refaire",
    };
    if (!labels[statusKey]) return null;
    return node("span", labels[statusKey], `badge status-${statusKey}`);
  }

  function renderApprovisionnement(appro) {
    const empty = $("appro-empty");
    const content = $("appro-content");
    const obsolete = $("appro-obsolete");
    const actions = $("appro-actions");
    if (!empty || !content || !obsolete || !actions) return;
    if (!appro) {
      hasApprovisionnement = false;
      empty.hidden = false;
      content.hidden = true;
      content.replaceChildren();
      content.classList.remove("appro-secondary");
      obsolete.hidden = true;
      actions.hidden = true;
      setApproBadge("none");
      return;
    }
    hasApprovisionnement = true;
    empty.hidden = true;
    content.hidden = false;
    actions.hidden = false;
    obsolete.hidden = !appro.obsolete;
    if (appro.obsolete) obsolete.textContent = STALE_APPRO_MESSAGE;
    const statusKey = appro.obsolete ? "obsolete" : "retained";
    setApproBadge(statusKey);
    content.classList.toggle("appro-secondary", Boolean(appro.obsolete));
    const strategy = (appro.snapshot && appro.snapshot.strategy) || {};
    const stops = strategy.stops || [];
    const summary = node("div", undefined, "appro-summary");
    const titleRow = node("div", undefined, "appro-strategy-row");
    titleRow.append(
      node(
        "p",
        appro.strategy_title || strategy.title || appro.strategy_key,
        "appro-strategy",
      ),
    );
    const inlineBadge = strategyStatusBadge(statusKey);
    if (inlineBadge) titleRow.append(inlineBadge);
    summary.append(
      titleRow,
      node(
        "p",
        `Choisie le ${new Date(appro.chosen_at).toLocaleDateString("fr-FR")}`,
        "muted",
      ),
    );
    if (stops.length) {
      summary.append(
        node(
          "p",
          stops.map((s) => `${s.name} (${s.supplier})`).join(" → "),
          "appro-agencies",
        ),
      );
    }
    const totals = node("dl", undefined, "appro-totals");
    const rows = [];
    if (appro.material_total != null) {
      rows.push(["Matériaux", `${money(appro.material_total)}`]);
    }
    const distanceCost =
      strategy.cost_breakdown && strategy.cost_breakdown.distance_cost;
    if (distanceCost != null) {
      rows.push(["Trajet", money(distanceCost)]);
    } else if (appro.total_distance_km != null || appro.travel_minutes != null) {
      const parts = [];
      if (appro.total_distance_km != null) {
        parts.push(`${number(appro.total_distance_km)} km`);
      }
      if (appro.travel_minutes != null) {
        parts.push(`${number(appro.travel_minutes)} min`);
      }
      rows.push(["Trajet", parts.join(" · ")]);
    }
    if (appro.estimated_procurement_cost != null) {
      rows.push(["Total estimé", money(appro.estimated_procurement_cost)]);
    }
    rows.forEach(([label, value]) => {
      const group = node("div");
      group.append(node("dt", label), node("dd", value));
      totals.append(group);
    });
    if (rows.length) summary.append(totals);

    const linesBlock = node("div", undefined, "appro-lines");
    linesBlock.append(node("h3", "Matériaux retenus"));
    (strategy.lines || []).forEach((line) => {
      const item = node("div", undefined, "appro-line");
      item.append(
        node(
          "strong",
          `${number(line.requested_quantity)} × ${line.product_name}`,
        ),
        node("p", `${money(line.line_total)} HT · ${line.supplier}`, "muted"),
      );
      linesBlock.append(item);
    });
    const shoppingLink = node("a", "Voir la liste d’achat", "button primary shopping-list-link");
    shoppingLink.href = `/chantiers/${chantierId}/liste-achat`;
    content.replaceChildren(summary, linesBlock, shoppingLink);
  }

  async function loadApprovisionnement() {
    try {
      const response = await fetch(`/api/chantiers/${chantierId}/approvisionnement`);
      if (response.status === 404) {
        renderApprovisionnement(null);
        return;
      }
      if (!response.ok) throw new Error("Impossible de charger l’approvisionnement.");
      renderApprovisionnement(await response.json());
    } catch (error) {
      renderApprovisionnement(null);
      if (error && error.message) {
        console.warn("Approvisionnement:", error.message);
      }
    }
  }

  async function saveChantier({ quiet = false } = {}) {
    if (!$("chantier-form").reportValidity()) return false;
    if (!reportQuantityFields($("cart-body"))) return false;
    saving = true;
    status("Enregistrement…");
    try {
      const body = {
        ...readFormMeta(),
        updated_at: $("updated_at").value,
        materiaux: lines.map((line, index) => ({
          product_id: line.product_id,
          quantite: line.quantity,
          ordre: index,
        })),
      };
      const response = await fetch(`/api/chantiers/${chantierId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await response.json().catch(() => ({}));
      if (response.status === 409) {
        status(CONFLICT_MESSAGE, true);
        return false;
      }
      if (!response.ok) {
        throw new Error(
          apiErrorMessage(payload, "Impossible d’enregistrer ce chantier."),
        );
      }
      currentChantier = payload;
      fillForm(payload);
      chantierNom = payload.nom;
      lines = payload.materiaux.map((line) => ({
        product_id: line.product_id,
        quantity: String(line.quantite),
      }));
      materialList.renderLines();
      dirty = false;
      showIdentityCompact();
      try {
        await loadApprovisionnement();
      } catch (approError) {
        console.warn("Approvisionnement après sauvegarde:", approError);
      }
      if (!quiet) status("Chantier enregistré.");
      return true;
    } catch (error) {
      status(error.message || "Connexion impossible. Réessayez.", true);
      return false;
    } finally {
      saving = false;
    }
  }

  $("identity-edit").addEventListener("click", () => {
    showIdentityEdit();
  });
  $("identity-cancel").addEventListener("click", () => {
    cancelIdentityEdit();
  });
  $("identity-save").addEventListener("click", async () => {
    const button = $("identity-save");
    button.disabled = true;
    try {
      const saved = await saveChantier({ quiet: true });
      if (saved) {
        showIdentityCompact();
        status("Informations enregistrées.");
      }
    } finally {
      button.disabled = false;
    }
  });

  $("save-button").addEventListener("click", async () => {
    if (identityEditing) cancelIdentityEdit();
    const button = $("save-button");
    button.disabled = true;
    $("compare-prices").disabled = true;
    try {
      await saveChantier();
    } finally {
      button.disabled = false;
      $("compare-prices").disabled = false;
    }
  });

  const goCompare = async () => {
    if (saving) return;
    if (identityEditing) cancelIdentityEdit();
    const button = $("compare-prices");
    const saveButton = $("save-button");
    button.disabled = true;
    saveButton.disabled = true;
    const emptyCompare = $("appro-compare-empty");
    if (emptyCompare) emptyCompare.disabled = true;
    try {
      if (dirty) {
        const saved = await saveChantier({ quiet: true });
        if (!saved) return;
      }
      window.location.assign(`/?chantier_id=${chantierId}`);
    } finally {
      button.disabled = false;
      saveButton.disabled = false;
      if (emptyCompare) emptyCompare.disabled = false;
    }
  };

  // Actions primaires AVANT les handlers appro (secondaires) :
  // une exception sur un bind secondaire ne doit pas empêcher Comparer / Supprimer.
  $("compare-prices").addEventListener("click", goCompare);
  const approCompareEmpty = $("appro-compare-empty");
  if (approCompareEmpty) {
    approCompareEmpty.addEventListener("click", goCompare);
  }

  $("delete-chantier").addEventListener("click", () => {
    if (identityEditing) cancelIdentityEdit();
    const box = $("delete-chantier-message");
    const panel = $("delete-chantier-panel");
    if (!box || !panel) return;
    box.replaceChildren(
      node("p", `Voulez-vous vraiment supprimer « ${chantierNom || "ce chantier"} » ?`),
      node(
        "p",
        "Cette action supprimera le chantier, sa liste de matériaux et son "
          + "approvisionnement retenu éventuel.",
      ),
    );
    panel.hidden = false;
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  $("delete-chantier-cancel").addEventListener("click", () => {
    const panel = $("delete-chantier-panel");
    if (panel) panel.hidden = true;
  });

  $("delete-chantier-confirm").addEventListener("click", async () => {
    const button = $("delete-chantier-confirm");
    button.disabled = true;
    try {
      const response = await fetch(
        `/api/chantiers/${chantierId}?updated_at=${encodeURIComponent($("updated_at").value)}`,
        { method: "DELETE" },
      );
      if (response.status === 204) {
        window.location.assign("/chantiers?supprime=1");
        return;
      }
      if (response.status === 409) {
        status(CONFLICT_MESSAGE, true);
        return;
      }
      if (response.status === 404) {
        status("Ce chantier a déjà été supprimé.", true);
        return;
      }
      throw new Error("Impossible de supprimer ce chantier.");
    } catch (error) {
      status(error.message || "Connexion impossible. Réessayez.", true);
    } finally {
      button.disabled = false;
    }
  });

  const approRecompare = $("appro-recompare");
  if (approRecompare) {
    approRecompare.addEventListener("click", goCompare);
  }

  const approClear = $("appro-clear");
  if (approClear) {
    approClear.addEventListener("click", async () => {
      if (!hasApprovisionnement) return;
      if (
        !window.confirm(
          "Retirer l’approvisionnement retenu ? Les besoins du chantier seront conservés.",
        )
      ) {
        return;
      }
      try {
        const response = await fetch(
          `/api/chantiers/${chantierId}/approvisionnement?updated_at=${encodeURIComponent($("updated_at").value)}`,
          { method: "DELETE" },
        );
        if (response.status === 409) {
          status(CONFLICT_MESSAGE, true);
          return;
        }
        if (response.status === 404) {
          status("Aucun approvisionnement retenu.", true);
          renderApprovisionnement(null);
          return;
        }
        if (!response.ok) throw new Error("Impossible de retirer ce choix.");
        const refreshed = await fetch(`/api/chantiers/${chantierId}`);
        if (refreshed.ok) {
          const chantier = await refreshed.json();
          currentChantier = chantier;
          fillForm(chantier);
          chantierNom = chantier.nom;
        }
        renderApprovisionnement(null);
        status("Approvisionnement retiré. Les matériaux du chantier sont conservés.");
      } catch (error) {
        status(error.message || "Connexion impossible. Réessayez.", true);
      }
    });
  }

  const params = new URLSearchParams(window.location.search);
  const retainedFlash = params.get("approvisionnement") === "retenu";
  if (retainedFlash) {
    params.delete("approvisionnement");
    const next = params.toString();
    window.history.replaceState(
      {},
      "",
      next ? `/chantiers/${chantierId}?${next}` : `/chantiers/${chantierId}`,
    );
  }

  try {
    const [catalog, response] = await Promise.all([
      fetchProducts(),
      fetch(`/api/chantiers/${chantierId}`),
    ]);
    products = catalog;
    if (response.status === 404) {
      status("Chantier introuvable.", true);
      $("compare-prices").disabled = true;
      $("delete-chantier").disabled = true;
      return;
    }
    if (!response.ok) throw new Error("Impossible de charger ce chantier.");
    const chantier = await response.json();
    currentChantier = chantier;
    fillForm(chantier);
    showIdentityCompact();
    chantierNom = chantier.nom;
    lines = chantier.materiaux.map((line) => ({
      product_id: line.product_id,
      quantity: String(line.quantite),
    }));
    materialList.renderProducts();
    materialList.renderLines();
    dirty = false;
    try {
      await loadApprovisionnement();
    } catch (approError) {
      console.warn("Approvisionnement au chargement:", approError);
    }
    if (retainedFlash) {
      status("Solution d’approvisionnement enregistrée.");
    } else {
      status();
    }
  } catch (error) {
    status(error.message || "Connexion impossible. Réessayez.", true);
    if (!$("updated_at").value) {
      $("compare-prices").disabled = true;
    }
  }

  window.addEventListener("beforeunload", (event) => {
    if (!dirty && !identityEditing) return;
    event.preventDefault();
    event.returnValue = "";
  });
}

const page =
  document.querySelector("[data-page]") &&
  document.querySelector("[data-page]").dataset.page;

if (page === "chantiers-list") loadList();
else if (page === "chantier-new") setupCreate();
else if (page === "chantier-detail") {
  setupDetail(Number(document.querySelector("[data-page]").dataset.chantierId));
}
