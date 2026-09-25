"use strict";

(function () {
  const root = document.querySelector("[data-shopping-list]");
  if (!root) return;

  const printBtn = document.getElementById("shopping-print");
  if (printBtn) {
    printBtn.addEventListener("click", () => window.print());
  }

  const boxes = Array.from(root.querySelectorAll("[data-shopping-taken]"));
  const progress = document.getElementById("shopping-progress");
  const progressText = document.getElementById("shopping-progress-text");
  const progressDone = document.getElementById("shopping-progress-done");
  if (!progress || !progressText || !boxes.length) return;

  const total = Number(progress.dataset.total || boxes.length) || boxes.length;

  function refresh() {
    const taken = boxes.filter((box) => box.checked).length;
    progressText.textContent = `${taken} / ${total} articles pris`;
    const complete = taken === total && total > 0;
    if (progressDone) progressDone.hidden = !complete;
    boxes.forEach((box) => {
      const line = box.closest(".shopping-line");
      if (line) line.classList.toggle("is-taken", box.checked);
    });
  }

  boxes.forEach((box) => box.addEventListener("change", refresh));
  refresh();
})();
