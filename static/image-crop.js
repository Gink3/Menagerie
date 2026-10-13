/* Image framing controls share ordered autosave and inline feedback. */
(() => {

  const cropBox = document.querySelector("#crop-preview");
  const cropForm = document.querySelector("#crop-form");
  const preview = document.querySelector("#crop-preview-image");
  const focalMode = document.querySelector("#focal-mode");
  const focusEnabled = document.querySelector("#use-focal-point");
  const focalX = document.querySelector("#focal-x");
  const focalY = document.querySelector("#focal-y");
  const focalSource = document.querySelector("#focal-source");
  const focalSourceImage = document.querySelector("#focal-source-image");
  const focalMarker = document.querySelector("#focal-marker");
  const cropX = document.querySelector("#crop-x");
  const cropY = document.querySelector("#crop-y");
  const cropZoom = document.querySelector("#crop-zoom");
  const ratioWidth = document.querySelector("#image-ratio-width");
  const ratioHeight = document.querySelector("#image-ratio-height");
  const cropStatus = document.querySelector("#crop-status");
  const cropZoomValue = document.querySelector("#crop-zoom-value");
  let dragStart = null;
  let saving;

  const updateCropPreview = () => {
    if (ratioWidth.validity.valid && ratioHeight.validity.valid) {
      cropBox.style.setProperty("--crop-ratio", Number(ratioWidth.value) / Number(ratioHeight.value));
    }
    focalMode.value = focusEnabled.checked ? "manual" : "legacy";
    window.MenagerieFraming.set(preview,
      focusEnabled.checked ? Number(focalX.value) : null,
      focusEnabled.checked ? Number(focalY.value) : null, Number(cropZoom.value));
    if (!focusEnabled.checked) {
      preview.style.objectPosition = `${cropX.value}% ${cropY.value}%`;
      preview.style.transformOrigin = `${cropX.value}% ${cropY.value}%`;
      preview.style.transform = `scale(${cropZoom.value})`;
    }
    focalMarker.hidden = !focusEnabled.checked;
    focalMarker.style.left = `${focalX.value}%`;
    focalMarker.style.top = `${focalY.value}%`;
    cropZoomValue.textContent = `${Number(cropZoom.value).toFixed(2)}×`;
  };

  const clamp = (value) => Math.max(0, Math.min(100, value));
  const clampZoom = (value) => Math.max(1, Math.min(3, value));

  const queueSaveCrop = () => saving.queue();

  cropBox.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    cropBox.setPointerCapture(event.pointerId);
    dragStart = {
      x: event.clientX,
      y: event.clientY,
      cropX: Number(cropX.value),
      cropY: Number(cropY.value),
      focalX: Number(focalX.value), focalY: Number(focalY.value),
    };
  });

  cropBox.addEventListener("pointermove", (event) => {
    if (!dragStart) {
      return;
    }
    event.preventDefault();
    const rect = cropBox.getBoundingClientRect();
    const sensitivityX = 100 / Math.max(rect.width, 1);
    const sensitivityY = 100 / Math.max(rect.height, 1);
    if (focusEnabled.checked && preview.naturalWidth) {
      const scale = Math.max(rect.width / preview.naturalWidth, rect.height / preview.naturalHeight) * Number(cropZoom.value);
      focalX.value = clamp(dragStart.focalX - (event.clientX - dragStart.x) * 100 / (preview.naturalWidth * scale));
      focalY.value = clamp(dragStart.focalY - (event.clientY - dragStart.y) * 100 / (preview.naturalHeight * scale));
    } else {
      cropX.value = clamp(dragStart.cropX - (event.clientX - dragStart.x) * sensitivityX);
      cropY.value = clamp(dragStart.cropY - (event.clientY - dragStart.y) * sensitivityY);
    }
    updateCropPreview();
    queueSaveCrop();
  });

  cropBox.addEventListener("pointerup", (event) => {
    dragStart = null;
    if (cropBox.hasPointerCapture(event.pointerId)) {
      cropBox.releasePointerCapture(event.pointerId);
    }
  });
  cropBox.addEventListener("pointercancel", () => { dragStart = null; });
  cropBox.addEventListener("wheel", (event) => {
    event.preventDefault();
    const nextZoom = clampZoom(Number(cropZoom.value) + (event.deltaY < 0 ? 0.05 : -0.05));
    cropZoom.value = nextZoom.toFixed(2);
    updateCropPreview();
    queueSaveCrop();
  });
  preview.addEventListener("dragstart", (event) => { event.preventDefault(); });
  cropZoom.addEventListener("input", () => {
    updateCropPreview();
    queueSaveCrop();
  });
  [ratioWidth, ratioHeight].forEach((input) => input.addEventListener("input", () => {
    updateCropPreview();
    queueSaveCrop();
  }));
  focalSource.addEventListener("click", (event) => {
    const rect = focalSourceImage.getBoundingClientRect();
    focalX.value = event.detail === 0 ? 50 : clamp((event.clientX - rect.left) / rect.width * 100);
    focalY.value = event.detail === 0 ? 50 : clamp((event.clientY - rect.top) / rect.height * 100);
    focusEnabled.checked = true;
    updateCropPreview();
    queueSaveCrop();
  });
  [focalX, focalY].forEach((input) => input.addEventListener("input", () => {
    focusEnabled.checked = true;
    updateCropPreview();
    queueSaveCrop();
  }));
  focusEnabled.addEventListener("change", () => {
    updateCropPreview();
    queueSaveCrop();
  });
  saving = window.MenagerieFeedback.autosave({
    form: cropForm, status: cropStatus, retry: document.querySelector("#retry-framing"),
    savedMessage: "Framing saved", validate: () => cropForm.checkValidity()
  });
  cropForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!await saving.flush()) return;
    // Leave only after the latest framing save completes.
    const body = new FormData(cropForm);
    body.set("finish_framing", "1");
    const result = await window.MenagerieFeedback.request(location.href, {
      method: "POST", headers: { "X-Requested-With": "fetch" }, body
    }).catch((error) => {
      window.MenagerieFeedback.set(cropStatus, "error", error.message);
      cropForm.querySelector('button[type="submit"]').textContent = "Retry Done";
    });
    if (result) location.assign(result.next);
  });
  updateCropPreview();
  document.querySelector("#reset-crop").addEventListener("click", () => {
    cropX.value = 50;
    cropY.value = 50;
    cropZoom.value = 1;
    focusEnabled.checked = false;
    focalX.value = focalY.value = 50;
    updateCropPreview();
    queueSaveCrop();
  });

})();
