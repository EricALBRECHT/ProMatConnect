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
  status("Chargement…");
  try {
    const response = await fetch("/api/chantiers");
    if (!response.ok) throw new Error("Impossible de charger les chantiers.");
    const chantiers = await response.json();
    if (!chantiers.length) {
      grid.hidden = true;
      grid.replaceChildren();
      empty.hidden = false;
      status();
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
    status();
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
      lines = payload.materiaux.map((line) => ({
        product_id: line.product_id,
        quantity: String(line.quantite),
      }));
      materialList.renderLines();
      dirty = false;
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

  try {
    const [catalog, response] = await Promise.all([
      fetchProducts(),
      fetch(`/api/chantiers/${chantierId}`),
    ]);
    products = catalog;
    if (response.status === 404) {
      status("Chantier introuvable.", true);
      $("compare-prices").disabled = true;
      return;
    }
    if (!response.ok) throw new Error("Impossible de charger ce chantier.");
    const chantier = await response.json();
    fillForm(chantier);
    lines = chantier.materiaux.map((line) => ({
      product_id: line.product_id,
      quantity: String(line.quantite),
    }));
    materialList.renderProducts();
    materialList.renderLines();
    dirty = false;
    status();
  } catch (error) {
    status(error.message || "Connexion impossible. Réessayez.", true);
    $("compare-prices").disabled = true;
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
