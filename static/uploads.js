/* Keep pending files visible and removable before the item is saved. */
document.querySelectorAll("[data-image-uploads]").forEach((section) => {
  if (typeof DataTransfer === "undefined") return;
  const input = section.querySelector('input[type="file"]');
  const previews = section.querySelector("[data-upload-previews]");
  const status = section.querySelector("[data-upload-status]");
  let files = [];
  let urls = [];
  const sync = () => {
    const transfer = new DataTransfer();
    files.forEach((file) => transfer.items.add(file));
    input.files = transfer.files;
    urls.forEach(URL.revokeObjectURL);
    urls = [];
    previews.replaceChildren();
    files.forEach((file, index) => {
      const card = document.createElement("figure");
      const image = document.createElement("img");
      const url = URL.createObjectURL(file);
      urls.push(url);
      image.src = url;
      image.alt = file.name;
      const caption = document.createElement("figcaption");
      const name = document.createElement("span");
      name.textContent = file.name;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "subtle";
      remove.textContent = "Remove";
      remove.setAttribute("aria-label", `Remove pending image ${file.name}`);
      remove.addEventListener("click", () => {
        files.splice(index, 1);
        sync();
        (previews.querySelectorAll("button")[Math.min(index, files.length - 1)] || input).focus();
      });
      caption.append(name, remove);
      card.append(image, caption);
      previews.append(card);
    });
    previews.hidden = !files.length;
    status.hidden = !files.length;
    status.textContent = `${files.length} ${files.length === 1 ? "photo" : "photos"} waiting to upload when you save the item.`;
    input.form.dispatchEvent(new CustomEvent("editor:changed"));
  };
  input.addEventListener("change", () => {
    for (const file of input.files) {
      if (!files.some((other) => other.name === file.name && other.size === file.size && other.lastModified === file.lastModified)) files.push(file);
    }
    sync();
  });
});
