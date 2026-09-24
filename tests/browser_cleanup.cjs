"use strict";

/**
 * Suivi strict des chantiers créés par un scénario navigateur.
 * Seuls ces IDs peuvent être supprimés au cleanup.
 */
function createChantierTracker() {
  const createdChantierIds = new Set();

  return {
    track(id) {
      const numeric = Number(id);
      if (!Number.isInteger(numeric) || numeric < 1) {
        throw new Error(`ID de chantier invalide à suivre : ${id}`);
      }
      createdChantierIds.add(numeric);
      return numeric;
    },

    ids() {
      return [...createdChantierIds];
    },

    has(id) {
      return createdChantierIds.has(Number(id));
    },

    /**
     * Supprime uniquement les IDs suivis. Ne liste jamais tous les chantiers
     * pour décider quoi effacer.
     */
    async cleanup(fetchImpl) {
      const fetchFn = fetchImpl || globalThis.fetch;
      if (typeof fetchFn !== "function") {
        throw new Error("fetch indisponible pour le cleanup.");
      }
      const deleted = [];
      for (const id of [...createdChantierIds]) {
        const read = await fetchFn(`/api/chantiers/${id}`);
        const readStatus = Number(read && read.status);
        if (readStatus === 404) {
          createdChantierIds.delete(id);
          continue;
        }
        if (readStatus < 200 || readStatus >= 300) {
          throw new Error(
            `Impossible de relire le chantier ${id} avant cleanup (HTTP ${readStatus}).`,
          );
        }
        const chantier = await read.json();
        const response = await fetchFn(
          `/api/chantiers/${id}?updated_at=${encodeURIComponent(chantier.updated_at)}`,
          { method: "DELETE" },
        );
        const delStatus = Number(response && response.status);
        if (delStatus !== 204 && delStatus !== 404) {
          throw new Error(`Cleanup refusé pour le chantier ${id} (HTTP ${delStatus}).`);
        }
        createdChantierIds.delete(id);
        deleted.push(id);
      }
      return deleted;
    },
  };
}

module.exports = { createChantierTracker };
