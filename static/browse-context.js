/* Return to the exact collection view, including its vertical and table scroll. */
(() => {
  const storage = {
    get: (key) => { try { return sessionStorage.getItem(key); } catch { return null; } },
    set: (key, value) => { try { sessionStorage.setItem(key, value); } catch { /* Storage is optional. */ } },
    remove: (key) => { try { sessionStorage.removeItem(key); } catch { /* Storage is optional. */ } }
  };
  document.querySelectorAll("a[data-collection-back]").forEach((link) => link.addEventListener("click", () => {
    storage.set("collection-return", new URL(link.href).pathname + new URL(link.href).search);
  }));
  document.addEventListener("submit", (event) => {
    const target = event.target.querySelector('[name="return_to"]')?.value;
    if (!target) return;
    const url = new URL(target, location.origin);
    if (url.origin === location.origin && /^\/collections\/\d+$/.test(url.pathname)) {
      storage.set("collection-return", url.pathname + url.search);
    }
  });
  if (!document.querySelector("[data-collection-browser]")) return;
  const url = location.pathname + location.search;
  const key = `collection-scroll:${url}`;
  const table = document.querySelector(".collection-table-wrap");
  const save = () => storage.set(key, JSON.stringify({ y: window.scrollY, x: table?.scrollLeft || 0 }));
  window.addEventListener("scroll", save, { passive: true });
  table?.addEventListener("scroll", save, { passive: true });
  window.addEventListener("pagehide", save);
  document.addEventListener("click", (event) => { if (event.target.closest("a")) save(); }, true);
  if (storage.get("collection-return") === url) {
    storage.remove("collection-return");
    let position;
    try { position = JSON.parse(storage.get(key)); } catch { return; }
    if (!position) return;
    const restore = () => {
      window.scrollTo(0, Number(position.y) || 0);
      if (table) table.scrollLeft = Number(position.x) || 0;
    };
    if (document.readyState === "complete") restore();
    else window.addEventListener("load", restore, { once: true });
  }
})();
