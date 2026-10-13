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
      const status = form.querySelector("[data-item-save-status]");
      if (status && !form.dataset.saving) status.textContent = changed() ? "Unsaved item details or uploads" : "No unsaved item changes";
      form.dispatchEvent(new CustomEvent("editor:dirty", { detail: { dirty: changed() } }));
    };
    form.addEventListener("input", update);
    form.addEventListener("change", update);
    form.addEventListener("editor:changed", update);
    form.addEventListener("editor:saved", () => { baseline = snapshot(); update(); });
    form.addEventListener("submit", (event) => {
      if (!event.defaultPrevented && !form.hasAttribute("data-background-save")) leaving = true;
    });
    window.addEventListener("beforeunload", (event) => {
      if (!leaving && changed()) { event.preventDefault(); event.returnValue = ""; }
    });
    window.addEventListener("pageshow", () => { leaving = false; update(); });
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize);
  else initialize();
})();
