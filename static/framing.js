/* Keep a saved source-image point centered, clamping at the image edges. */
(() => {
  const observed = new WeakSet();
  const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));
  const render = (image) => {
    if (!image.hasAttribute("data-focal-x") || !image.naturalWidth) return;
    const frame = image.parentElement;
    const width = frame.clientWidth;
    const height = frame.clientHeight;
    if (!width || !height) return;
    const zoom = Number(image.dataset.focalZoom || 1);
    const scale = Math.max(width / image.naturalWidth, height / image.naturalHeight) * zoom;
    const renderedWidth = image.naturalWidth * scale;
    const renderedHeight = image.naturalHeight * scale;
    image.classList.add("focal-image");
    image.style.width = `${renderedWidth}px`;
    image.style.height = `${renderedHeight}px`;
    image.style.left = `${clamp(width / 2 - Number(image.dataset.focalX) / 100 * renderedWidth, width - renderedWidth, 0)}px`;
    image.style.top = `${clamp(height / 2 - Number(image.dataset.focalY) / 100 * renderedHeight, height - renderedHeight, 0)}px`;
    image.style.transform = "none";
  };
  const observe = (image) => {
    if (observed.has(image)) return;
    observed.add(image);
    image.addEventListener("load", () => render(image));
    const observer = new ResizeObserver(() => render(image));
    observer.observe(image.parentElement);
  };
  const set = (image, x, y, zoom) => {
    if (x === null || y === null) {
      image.removeAttribute("data-focal-x");
      image.removeAttribute("data-focal-y");
      image.removeAttribute("data-focal-zoom");
      image.classList.remove("focal-image");
      ["width", "height", "left", "top", "transform"].forEach((property) => image.style.removeProperty(property));
      return;
    }
    image.dataset.focalX = x;
    image.dataset.focalY = y;
    image.dataset.focalZoom = zoom;
    observe(image);
    render(image);
  };
  window.MenagerieFraming = { set };
  document.querySelectorAll("img[data-focal-x]").forEach((image) => {
    observe(image);
    render(image);
  });
})();
