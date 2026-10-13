/* Progressive enhancement: the original comma-separated field still submits. */
document.querySelectorAll(".tag-editor").forEach((editor) => {
  const valueField = editor.querySelector('[name="tags"]');
  const input = editor.querySelector("#tag-entry");
  const chips = editor.querySelector(".tag-chip-list");
  const list = editor.querySelector(".tag-suggestions");
  const status = editor.querySelector("#tag-status");
  const known = JSON.parse(editor.dataset.knownTags);
  const normalize = (value) => value.trim().toLowerCase();
  let tags = [...new Set(valueField.value.split(/[,\n]/).map(normalize).filter(Boolean))];
  let choices = [];
  let active = -1;

  const closeSuggestions = () => {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
  };
  const showActive = () => {
    [...list.children].forEach((option, index) => option.setAttribute("aria-selected", String(index === active)));
    if (active >= 0) {
      input.setAttribute("aria-activedescendant", list.children[active].id);
      list.children[active].scrollIntoView({ block: "nearest" });
    } else input.removeAttribute("aria-activedescendant");
  };
  const sync = () => {
    valueField.value = tags.join(", ");
    valueField.form.dispatchEvent(new CustomEvent("editor:changed"));
    chips.replaceChildren();
    tags.forEach((tag) => {
      const chip = document.createElement("span");
      chip.className = "tag-chip";
      chip.setAttribute("role", "listitem");
      const text = document.createElement("span");
      text.textContent = tag;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "×";
      remove.setAttribute("aria-label", `Remove tag ${tag}`);
      remove.addEventListener("mousedown", (event) => event.preventDefault());
      remove.addEventListener("click", () => {
        tags = tags.filter((value) => value !== tag);
        sync();
        status.textContent = `Removed ${tag}.`;
        input.focus();
        suggest();
      });
      chip.append(text, remove);
      chips.append(chip);
    });
  };
  const add = (raw) => {
    const tag = normalize(raw);
    if (tag && !tags.includes(tag)) {
      tags.push(tag);
      status.textContent = `Added ${tag}.`;
    }
    sync();
  };
  const commit = () => {
    input.value.split(/[,\n]/).forEach(add);
    input.value = "";
    closeSuggestions();
  };
  const choose = (tag) => {
    add(tag);
    input.value = "";
    input.focus();
    suggest();
  };
  const suggest = () => {
    const query = normalize(input.value);
    choices = known.filter((tag) => !tags.includes(tag) && tag.includes(query)).slice(0, 8);
    if (query && !tags.includes(query) && !known.includes(query)) choices.push(query);
    list.replaceChildren();
    active = -1;
    choices.forEach((tag, index) => {
      const option = document.createElement("li");
      option.id = `tag-option-${index}`;
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", "false");
      option.textContent = known.includes(tag) ? tag : `Add “${tag}”`;
      option.addEventListener("mousedown", (event) => event.preventDefault());
      option.addEventListener("click", () => choose(tag));
      list.append(option);
    });
    list.hidden = !choices.length;
    input.setAttribute("aria-expanded", String(Boolean(choices.length)));
    input.removeAttribute("aria-activedescendant");
  };
  input.addEventListener("input", (event) => {
    if (event.isComposing) return;
    if (/[,\n]/.test(input.value)) {
      const parts = input.value.split(/[,\n]/);
      input.value = parts.pop();
      parts.forEach(add);
    }
    suggest();
  });
  input.addEventListener("keydown", (event) => {
    if (event.isComposing) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (list.hidden) suggest();
      if (!choices.length) return;
      active = active < 0 ? (event.key === "ArrowDown" ? 0 : choices.length - 1)
        : (active + (event.key === "ArrowDown" ? 1 : -1) + choices.length) % choices.length;
      showActive();
    } else if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      if (event.key === "Enter" && active >= 0 && !list.hidden) choose(choices[active]);
      else commit();
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeSuggestions();
    } else if (event.key === "Backspace" && !input.value) {
      chips.lastElementChild?.querySelector("button").focus();
    }
  });
  input.addEventListener("focus", suggest);
  input.addEventListener("blur", () => {
    if (input.value.trim()) commit();
    else closeSuggestions();
  });
  valueField.form.addEventListener("submit", commit);
  valueField.form.addEventListener("formdata", (event) => {
    commit();
    event.formData.set("tags", valueField.value);
  });
  editor.querySelector(".tag-input-box").addEventListener("click", (event) => {
    if (!event.target.closest("button")) input.focus();
  });
  sync();
  valueField.type = "hidden";
  editor.querySelector("label").htmlFor = input.id;
  editor.querySelector(".tag-enhanced").hidden = false;
});
