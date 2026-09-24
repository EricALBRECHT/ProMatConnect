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
  "Les besoins du chantier ont changé depuis le choix de cet "
  + "approvisionnement. Une nouvelle comparaison est recommandée.";
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
}

async function loadList() {
  const grid = $("chantiers-grid");
  const empty = $("chantiers-empty");
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
  try {
    const response = await fetch("/api/chantiers");
    if (!response.ok) throw new Error("Impossible de charger les chantiers.");
    const chantiers = await response.json();
    if (!chantiers.length) {
      grid.hidden = true;
      grid.replaceChildren();
      empty.hidden = false;
      if (!justDeleted) status();
      return;
    }
    empty.hidden = true;
    grid.hidden = false;
    grid.replaceChildren(
      ...chantiers.map((chantier) => {
        const card = node("article", undefined, "chantier-card");
        card.append(node("h2", chantier.nom));
        if (chantier.client) {
          card.append(node("p", chantier.client, "chantier-client"));
        }
        card.append(node("p", chantier.adresse, "chantier-address"));
        if (chantier.date_prevue) {
          card.append(
            node("p", `Prévu le ${formatDate(chantier.date_prevue)}`, "muted"),
          );
        }
        const count = chantier.materiaux.length;
        card.append(
          node(
            "p",
            `${count} matériau${count > 1 ? "x" : ""}`,
            "chantier-count",
          ),
        );
        const open = node("a", "Ouvrir", "button secondary");
        open.href = `/chantiers/${chantier.id}`;
        card.append(open);
        return card;
      }),
    );
    if (!justDeleted) status();
  } catch (error) {
    empty.hidden = true;
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

  $("nom").addEventListener("input", markDirty);
  $("client").addEventListener("input", markDirty);
  $("adresse").addEventListener("input", markDirty);
  $("date_prevue").addEventListener("input", markDirty);
  $("notes").addEventListener("input", markDirty);

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
      obsolete.hidden = true;
      actions.hidden = true;
      return;
    }
    hasApprovisionnement = true;
    empty.hidden = true;
    content.hidden = false;
    actions.hidden = false;
    obsolete.hidden = !appro.obsolete;
    if (appro.obsolete) obsolete.textContent = STALE_APPRO_MESSAGE;
    const strategy = (appro.snapshot && appro.snapshot.strategy) || {};
    const stops = strategy.stops || [];
    const summary = node("div", undefined, "appro-summary");
    summary.append(
      node("p", `Stratégie : ${appro.strategy_title || strategy.title || appro.strategy_key}`),
      node(
        "p",
        `Choisi le ${new Date(appro.chosen_at).toLocaleString("fr-FR")}`,
        "muted",
      ),
    );
    if (stops.length) {
      summary.append(
        node(
          "p",
          `Agence(s) : ${stops.map((s) => `${s.name} (${s.supplier})`).join(" → ")}`,
        ),
      );
    }
    if (appro.material_total != null) {
      summary.append(node("p", `Total matériaux : ${money(appro.material_total)} HT`));
    }
    if (appro.total_distance_km != null) {
      summary.append(node("p", `Distance : ${number(appro.total_distance_km)} km`));
    }
    if (appro.travel_minutes != null) {
      summary.append(node("p", `Trajet : ${number(appro.travel_minutes)} min`));
    }
    const distanceCost =
      strategy.cost_breakdown && strategy.cost_breakdown.distance_cost;
    if (distanceCost != null) {
      summary.append(
        node("p", `Coût trajet (indicateur) : ${money(distanceCost)}`),
      );
    }
    if (appro.estimated_procurement_cost != null) {
      summary.append(
        node(
          "strong",
          `Total estimé d’approvisionnement : ${money(appro.estimated_procurement_cost)}`,
        ),
      );
    }
    const linesBlock = node("div", undefined, "appro-lines");
    linesBlock.append(node("h3", "Matériaux retenus"));
    (strategy.lines || []).forEach((line) => {
      const item = node("div", undefined, "appro-line");
      item.append(
        node("strong", line.product_name),
        node(
          "p",
          `${number(line.requested_quantity)} ${line.reference_unit} demandés · `
            + `${line.packs} × ${line.supplier_unit} · ${money(line.line_total)} HT`,
        ),
        node(
          "p",
          `${line.supplier} · réf. ${line.supplier_reference} · ${money(line.pack_price)} / pack`,
          "muted",
        ),
      );
      linesBlock.append(item);
    });
    content.replaceChildren(summary, linesBlock);
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
      // Ne pas faire échouer l'init / la sauvegarde : l'appro est secondaire.
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
      fillForm(payload);
      chantierNom = payload.nom;
      lines = payload.materiaux.map((line) => ({
        product_id: line.product_id,
        quantity: String(line.quantite),
      }));
      materialList.renderLines();
      dirty = false;
      // L'enregistrement métier a réussi : un souci d'affichage appro ne doit pas le faire échouer
      // (sinon « Comparer les prix » en mode dirty refuse la navigation).
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

  $("save-button").addEventListener("click", async () => {
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

  // Actions primaires AVANT les handlers appro (secondaires) :
  // une exception sur un bind secondaire ne doit pas empêcher Comparer / Supprimer.
  $("compare-prices").addEventListener("click", async () => {
    if (saving) return;
    const button = $("compare-prices");
    const saveButton = $("save-button");
    button.disabled = true;
    saveButton.disabled = true;
    try {
      if (dirty) {
        const saved = await saveChantier({ quiet: true });
        if (!saved) return;
      }
      window.location.assign(`/?chantier_id=${chantierId}`);
    } finally {
      button.disabled = false;
      saveButton.disabled = false;
    }
  });

  $("delete-chantier").addEventListener("click", () => {
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
    approRecompare.addEventListener("click", () => {
      $("compare-prices").click();
    });
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
    fillForm(chantier);
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
    status();
  } catch (error) {
    status(error.message || "Connexion impossible. Réessayez.", true);
    // Ne désactiver Comparer que si le chantier lui-même n'a pas pu être chargé.
    if (!$("updated_at").value) {
      $("compare-prices").disabled = true;
    }
  }

  window.addEventListener("beforeunload", (event) => {
    if (!dirty) return;
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
