/* One toolbar manages the selected photograph; rows retain their own settings. */
(() => {
  const manager = document.querySelector(".item-image-manager");
  if (!manager) return;
  const form = manager.querySelector(".image-strip");
  const status = manager.querySelector("#gallery-image-status");
  const selectedStatus = manager.querySelector("#selected-image-status");
  const selectedId = manager.querySelector("#selected-image-id");
  const frameLink = manager.querySelector("#frame-selected-image");
  const deleteForm = manager.querySelector("[data-background-delete]");
  const deleteButton = manager.querySelector("#delete-selected-image");
  const moves = [...manager.querySelectorAll("[data-image-move]")];
  const rows = () => [...form.querySelectorAll(".item-image-row")];
  let selected = null;
  let dragged = null;
  let timer;
  const openedFrames = new Map();
  const select = (row) => {
    selected = row;
    rows().forEach((other) => other.classList.toggle("selected", other === row));
    selectedId.value = row?.dataset.imageId || "";
    selectedStatus.textContent = row ? row.dataset.imageName : "Select a photograph below.";
    frameLink.hidden = !row;
    if (row) frameLink.href = row.dataset.frameUrl;
    else frameLink.removeAttribute("href");
    deleteButton.disabled = !row;
    moves.forEach((button) => {
      const index = rows().indexOf(row);
      button.disabled = !row || index + Number(button.dataset.imageMove) < 0 || index + Number(button.dataset.imageMove) >= rows().length;
    });
  };
  const renumber = () => {
    let position = 1;
    rows().forEach((row) => {
      const enabled = row.querySelector(".gallery-image-toggle").checked;
      row.querySelector(".gallery-cover-badge").hidden = !enabled || position !== 1;
      if (enabled) row.querySelector(".gallery-image-order").value = position++;
    });
    manager.querySelector("#gallery-selection-summary").textContent = position > 1
      ? "The first enabled photograph represents this item in the random gallery."
      : "No photographs selected for galleries.";
    select(selected);
  };
  const save = async () => {
    status.textContent = "Saving photo settings…";
    try {
      const response = await fetch(form.action, { method: "POST", headers: { "X-Requested-With": "fetch" }, body: new FormData(form) });
      if (!response.ok) throw new Error();
      status.textContent = "Photo settings saved";
    } catch { status.textContent = "Could not save photo settings. Change a selection to retry."; }
  };
  const queueSave = () => { clearTimeout(timer); timer = setTimeout(save, 200); };
  form.addEventListener("submit", (event) => { event.preventDefault(); renumber(); save(); });
  moves.forEach((button) => button.addEventListener("click", () => {
    const target = rows()[rows().indexOf(selected) + Number(button.dataset.imageMove)];
    if (!selected || !target) return;
    if (Number(button.dataset.imageMove) < 0) target.before(selected);
    else target.after(selected);
    renumber(); queueSave();
    if (button.disabled) selected.focus();
  }));
  rows().forEach((row) => {
    row.addEventListener("click", (event) => { if (!event.target.closest("a, button, summary, details")) select(row); });
    row.addEventListener("keydown", (event) => {
      if (event.target === row && ["Enter", " "].includes(event.key)) { event.preventDefault(); select(row); }
    });
    row.querySelector(".gallery-image-toggle").addEventListener("change", () => { select(row); renumber(); queueSave(); });
    row.addEventListener("dragstart", (event) => {
      if (event.target.closest("input, a, button, summary, details")) { event.preventDefault(); return; }
      select(row); dragged = row; row.classList.add("dragging"); event.dataTransfer.effectAllowed = "move";
    });
    row.addEventListener("dragend", () => { dragged = null; rows().forEach((other) => other.classList.remove("dragging", "drag-over")); });
    row.addEventListener("dragover", (event) => { if (dragged) { event.preventDefault(); row.classList.add("drag-over"); } });
    row.addEventListener("dragleave", () => row.classList.remove("drag-over"));
    row.addEventListener("drop", (event) => {
      event.preventDefault(); row.classList.remove("drag-over");
      if (!dragged || dragged === row) return;
      if (rows().indexOf(dragged) < rows().indexOf(row)) row.after(dragged);
      else row.before(dragged);
      renumber(); queueSave();
    });
  });
  deleteForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selected || !confirm("Delete the selected image? This cannot be undone.")) return;
    const row = selected;
    const data = new FormData(deleteForm);
    deleteButton.disabled = true;
    try {
      const response = await fetch(deleteForm.action, { method: "POST", headers: { "X-Requested-With": "fetch" }, body: data });
      if (!response.ok) throw new Error();
      row.remove();
      select(selected === row ? rows()[0] || null : selected);
      renumber();
      selectedStatus.textContent = "Image deleted.";
      form.focus();
    } catch { selectedStatus.textContent = "Could not delete image. Try again."; deleteButton.disabled = !selected; }
  });
  frameLink.addEventListener("click", () => { if (selected) openedFrames.set(selected.dataset.imageId, selected.dataset.frameUrl); });
  window.addEventListener("focus", async () => {
    for (const [id, url] of openedFrames) {
      const row = rows().find((row) => row.dataset.imageId === id);
      if (!row) continue;
      try {
        const response = await fetch(url, { headers: { "X-Requested-With": "fetch" } });
        if (!response.ok) continue;
        const settings = await response.json();
        row.querySelector(".image-ratio-summary").textContent = `Random gallery: ${settings.gallery_ratio_width} × ${settings.gallery_ratio_height}`;
        const image = row.querySelector("img");
        window.MenagerieFraming.set(image, settings.focal_x, settings.focal_y, settings.crop_zoom);
        if (settings.focal_x === null || settings.focal_y === null) {
          image.style.objectPosition = `${settings.crop_x}% ${settings.crop_y}%`;
          image.style.transformOrigin = `${settings.crop_x}% ${settings.crop_y}%`;
          image.style.transform = `scale(${settings.crop_zoom})`;
        }
      } catch { /* Retain the current preview until the next refresh. */ }
    }
  });
  renumber();
})();
