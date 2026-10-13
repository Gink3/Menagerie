/* Protect edits without treating independently saved photo settings as item edits. */
(() => {
  const initialize = () => document.querySelectorAll("form[data-dirty-form]").forEach((form) => {
    const snapshot = () => JSON.stringify([...form.elements].filter((control) =>
      (control.name || control.hasAttribute("data-dirty-value")) && control.name !== "csrf_token" &&
      !["submit", "button"].includes(control.type)
    ).map((control) => [control.name || control.id, control.type === "file"
      ? [...control.files].map((file) => [file.name, file.size, file.lastModified])
      : ["checkbox", "radio"].includes(control.type) ? control.checked : control.value]));
    let baseline = snapshot();
    let leaving = false;
    const changed = () => snapshot() !== baseline;
    const update = () => {
      form.classList.toggle("has-unsaved-changes", changed());
      const status = document.querySelector("[data-item-save-status]");
      if (status && !form.dataset.saving) window.MenagerieFeedback.set(status, changed() ? "pending" : "saved",
        changed() ? "Unsaved item details or uploads" : "No unsaved item changes");
      form.dispatchEvent(new CustomEvent("editor:dirty", { detail: { dirty: changed() } }));
    };
    form.addEventListener("input", update);
    form.addEventListener("change", update);
    form.addEventListener("editor:changed", update);
    form.addEventListener("editor:saved", () => { baseline = snapshot(); update(); });
    form.addEventListener("submit", (event) => {
      if (!event.defaultPrevented && !form.hasAttribute("data-background-save")) leaving = true;
    });
    if (form.hasAttribute("data-background-save")) {
      const status = document.querySelector("[data-item-save-status]");
      const submit = document.querySelector(`button[type="submit"][form="${form.id}"]`) || form.querySelector('button[type="submit"]');
      form.querySelector('[name="name"]').addEventListener("input", (event) => {
        event.target.setCustomValidity(event.target.value.trim() ? "" : "Enter an item name.");
      });
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (form.dataset.saving) return;
        form.dataset.saving = "true";
        submit.disabled = true;
        window.MenagerieFeedback.set(status, "saving", "Saving item…");
        let controls = [];
        const photos = document.querySelector(".item-image-manager");
        if (photos) photos.inert = true;
        try {
          const photoSaves = await Promise.all([...window.MenagerieFeedback.pendingSaves].map((save) => save.flush()));
          if (photoSaves.some((ok) => !ok)) throw new Error("Photo settings could not save. Use Retry in Photographs, then save the item.");
          const body = new FormData(form);
          controls = [...form.elements].filter((control) => !control.disabled);
          controls.forEach((control) => { control.disabled = true; });
          const result = await window.MenagerieFeedback.request(form.action || location.href, {
            method: "POST", headers: { "X-Requested-With": "fetch" }, body
          });
          leaving = true;
          location.assign(result.next);
        } catch (error) {
          window.MenagerieFeedback.set(status, "error", error.message || "Could not save. Try again.");
          submit.textContent = "Retry save";
        } finally {
          controls.forEach((control) => { control.disabled = false; });
          if (photos) photos.inert = false;
          delete form.dataset.saving;
          submit.disabled = false;
        }
      });
    }
    window.addEventListener("beforeunload", (event) => {
      if (!leaving && changed()) { event.preventDefault(); event.returnValue = ""; }
    });
    window.addEventListener("pageshow", () => { leaving = false; update(); });
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize);
  else initialize();
})();
