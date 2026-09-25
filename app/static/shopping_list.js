"use strict";

(function () {
  const root = document.querySelector("[data-shopping-list]");
  if (!root) return;

  const chantierId = root.dataset.chantierId;
  const printBtn = document.getElementById("shopping-print");
  if (printBtn) {
    printBtn.addEventListener("click", () => window.print());
  }

  const statusEl = document.getElementById("shopping-save-status");
  const progress = document.getElementById("shopping-progress");
  const progressText = document.getElementById("shopping-progress-text");
  const progressDone = document.getElementById("shopping-progress-done");
  const lines = Array.from(root.querySelectorAll(".shopping-line[data-line-key]"));
  if (!chantierId || !lines.length) return;

  const money = (value) =>
    new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR" }).format(
      Number(value),
    );
  const qty = (value) =>
    new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 3 }).format(Number(value));

  function setStatus(message, isError) {
    if (!statusEl) return;
    statusEl.hidden = !message;
    statusEl.textContent = message || "";
    statusEl.classList.toggle("is-error", Boolean(isError));
  }

  function parseOptionalNumber(input) {
    const raw = String(input.value || "").trim();
    if (raw === "") return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  }

  function refreshProgress() {
    const total = lines.length;
    const taken = lines.filter((line) => line.querySelector("[data-shopping-taken]")?.checked)
      .length;
    if (progressText) progressText.textContent = `${taken} / ${total} articles pris`;
    if (progressDone) progressDone.hidden = !(taken === total && total > 0);
    if (progress) progress.dataset.taken = String(taken);
    const takenEl = root.querySelector("[data-actual-taken]");
    if (takenEl) takenEl.textContent = `${taken} / ${total}`;
  }

  function refreshLineDisplays(line) {
    const qtyInput = line.querySelector("[data-shopping-qty]");
    const priceInput = line.querySelector("[data-shopping-price]");
    const q = parseOptionalNumber(qtyInput);
    const p = parseOptionalNumber(priceInput);
    const printQty = line.querySelector("[data-print-qty]");
    const printPrice = line.querySelector("[data-print-price]");
    const sousTotalEl = line.querySelector("[data-sous-total-reel]");
    const ecartEl = line.querySelector("[data-ecart]");
    const planned = Number(line.dataset.lineTotal || 0);
    if (printQty) printQty.textContent = q == null ? "—" : `${qty(q)} packs`;
    if (printPrice) printPrice.textContent = p == null ? "—" : money(p);
    if (q != null && p != null) {
      const sous = Math.round(q * p * 100) / 100;
      const ecart = Math.round((sous - planned) * 100) / 100;
      if (sousTotalEl) sousTotalEl.textContent = `${money(sous)} HT`;
      if (ecartEl) ecartEl.textContent = money(ecart);
    } else {
      if (sousTotalEl) sousTotalEl.textContent = "—";
      if (ecartEl) ecartEl.textContent = "—";
    }
  }

  function refreshActualTotals() {
    let renseignes = 0;
    let material = 0;
    let planned = 0;
    lines.forEach((line) => {
      const q = parseOptionalNumber(line.querySelector("[data-shopping-qty]"));
      const p = parseOptionalNumber(line.querySelector("[data-shopping-price]"));
      if (q == null || p == null) return;
      renseignes += 1;
      material += q * p;
      planned += Number(line.dataset.lineTotal || 0);
    });
    const rEl = root.querySelector("[data-actual-renseignes]");
    const mEl = root.querySelector("[data-actual-material]");
    const eEl = root.querySelector("[data-actual-ecart]");
    if (rEl) rEl.textContent = `${renseignes} / ${lines.length}`;
    if (renseignes === 0) {
      if (mEl) mEl.textContent = "—";
      if (eEl) eEl.textContent = "—";
      return;
    }
    material = Math.round(material * 100) / 100;
    const ecart = Math.round((material - planned) * 100) / 100;
    if (mEl) mEl.textContent = `${money(material)} HT`;
    if (eEl) eEl.textContent = money(ecart);
  }

  function applyServerLine(line, data) {
    if (data.updated_at) line.dataset.updatedAt = data.updated_at;
    const box = line.querySelector("[data-shopping-taken]");
    const qtyInput = line.querySelector("[data-shopping-qty]");
    const priceInput = line.querySelector("[data-shopping-price]");
    if (box) box.checked = Boolean(data.pris);
    // Ne pas écraser une saisie locale plus récente que la réponse.
    if (qtyInput && document.activeElement !== qtyInput) {
      qtyInput.value = data.quantite_reelle == null ? "" : String(data.quantite_reelle);
    }
    if (priceInput && document.activeElement !== priceInput) {
      priceInput.value = data.prix_reel == null ? "" : String(data.prix_reel);
    }
    line.classList.toggle("is-taken", Boolean(data.pris));
    refreshLineDisplays(line);
    refreshProgress();
    refreshActualTotals();
  }

  async function saveLineNow(line, { prefilling } = {}) {
    const box = line.querySelector("[data-shopping-taken]");
    const qtyInput = line.querySelector("[data-shopping-qty]");
    const priceInput = line.querySelector("[data-shopping-price]");
    const pris = Boolean(box?.checked);
    let quantite = parseOptionalNumber(qtyInput);
    let prix = parseOptionalNumber(priceInput);

    if (prefilling && pris) {
      if (quantite == null) {
        quantite = Number(line.dataset.packs || 0);
        qtyInput.value = String(quantite);
      }
      if (prix == null) {
        prix = Number(line.dataset.packPrice || 0);
        priceInput.value = String(prix);
      }
    }

    const body = {
      pris,
      quantite_reelle: quantite,
      prix_reel: prix,
      updated_at: line.dataset.updatedAt || null,
    };
    const key = encodeURIComponent(line.dataset.lineKey);
    setStatus("Enregistrement…");
    const response = await fetch(
      `/api/chantiers/${chantierId}/liste-achat/lignes/${key}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (response.status === 409) {
      setStatus(
        (typeof payload?.detail === "string" && payload.detail) ||
          "Conflit : rechargez la page.",
        true,
      );
      throw new Error("conflict");
    }
    if (!response.ok) {
      const detail =
        typeof payload?.detail === "string"
          ? payload.detail
          : "Impossible d’enregistrer cette ligne.";
      setStatus(detail, true);
      throw new Error("save-failed");
    }
    applyServerLine(line, payload);
    setStatus("Enregistré");
    window.setTimeout(() => {
      if (statusEl && statusEl.textContent === "Enregistré") setStatus("");
    }, 1500);
  }

  function enqueueSave(line, options) {
    const prev = line._saveChain || Promise.resolve();
    line._saveChain = prev
      .catch(() => {})
      .then(() => saveLineNow(line, options))
      .catch(() => {});
    return line._saveChain;
  }

  lines.forEach((line) => {
    const box = line.querySelector("[data-shopping-taken]");
    const qtyInput = line.querySelector("[data-shopping-qty]");
    const priceInput = line.querySelector("[data-shopping-price]");
    refreshLineDisplays(line);

    box?.addEventListener("change", () => {
      line.classList.toggle("is-taken", box.checked);
      enqueueSave(line, { prefilling: true });
    });
    qtyInput?.addEventListener("blur", () => enqueueSave(line));
    priceInput?.addEventListener("blur", () => enqueueSave(line));
  });

  refreshProgress();
  refreshActualTotals();
})();
