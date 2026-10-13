/* Shared inline feedback and ordered, retryable saves for image settings. */
(() => {
  const pendingSaves = new Set();
  const set = (status, state, message) => {
    status.dataset.state = state;
    status.setAttribute("role", state === "error" ? "alert" : "status");
    status.textContent = message;
  };
  const request = async (url, options) => {
    const response = await fetch(url, options);
    if (response.redirected || !response.ok || !response.headers.get("Content-Type")?.includes("application/json")) {
      const message = response.status === 413 ? "The upload is too large. Remove a file or choose smaller images."
        : response.status === 400 ? "Check your entries and try again. If your session expired, reload after keeping a copy of your changes."
        : response.status === 403 || response.redirected ? "Your session or access changed. Your entries remain on this page."
        : "Could not save. Your changes remain on this page. Try again.";
      throw new Error(message);
    }
    const result = await response.json();
    if (!result.ok) throw new Error(result.error || "Could not save. Try again.");
    return result;
  };
  const autosave = ({ form, status, retry, savedMessage, validate = () => true }) => {
    let revision = 0;
    let saved = 0;
    let timer;
    let running;
    const save = async () => {
      clearTimeout(timer);
      if (running) {
        const ok = await running;
        return ok && saved < revision ? save() : ok;
      }
      if (saved === revision) return true;
      if (!validate()) {
        set(status, "error", "Check the highlighted entries before saving.");
        retry.hidden = false;
        return false;
      }
      const version = revision;
      retry.hidden = true;
      set(status, "saving", "Saving…");
      const body = new FormData(form);
      running = (async () => {
        try {
          await request(form.action || location.href, { method: "POST", headers: { "X-Requested-With": "fetch" }, body });
          saved = version;
          set(status, "saved", savedMessage);
          return true;
        } catch (error) {
          set(status, "error", error.message || "Could not save. Try again.");
          retry.hidden = false;
          return false;
        }
      })();
      const ok = await running;
      running = null;
      if (ok && saved < revision) return save();
      return ok;
    };
    const controller = {
      queue: () => {
        revision++;
        clearTimeout(timer);
        set(status, "pending", "Changes waiting to save…");
        timer = setTimeout(save, 250);
      },
      flush: () => saved < revision || running ? save() : Promise.resolve(true),
      dirty: () => saved < revision
    };
    retry.addEventListener("click", () => { if (validate()) save(); else form.reportValidity(); });
    pendingSaves.add(controller);
    window.addEventListener("beforeunload", (event) => {
      if (controller.dirty()) { event.preventDefault(); event.returnValue = ""; }
    });
    return controller;
  };
  let errorId = 0;
  const clearError = (control) => {
    if (!control.dataset.inlineError) return;
    document.getElementById(control.dataset.inlineError)?.remove();
    const ids = (control.getAttribute("aria-describedby") || "").split(" ").filter((id) => id && id !== control.dataset.inlineError);
    if (ids.length) control.setAttribute("aria-describedby", ids.join(" "));
    else control.removeAttribute("aria-describedby");
    control.removeAttribute("aria-invalid");
    delete control.dataset.inlineError;
  };
  document.addEventListener("invalid", (event) => {
    const control = event.target;
    clearError(control);
    const error = document.createElement("span");
    error.className = "field-error";
    error.id = `field-error-${++errorId}`;
    error.textContent = control.validationMessage;
    control.dataset.inlineError = error.id;
    control.setAttribute("aria-invalid", "true");
    control.setAttribute("aria-describedby", `${control.getAttribute("aria-describedby") || ""} ${error.id}`.trim());
    control.insertAdjacentElement("afterend", error);
  }, true);
  document.addEventListener("input", (event) => {
    const control = event.target;
    if (!control.validity || !control.dataset.inlineError) return;
    if (control.validity.valid) clearError(control);
    else document.getElementById(control.dataset.inlineError).textContent = control.validationMessage;
  });
  window.MenagerieFeedback = { set, request, autosave, pendingSaves };
})();
