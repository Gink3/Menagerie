/* Show dropdown settings only when the selected field type needs them. */
document.querySelectorAll("[data-dropdown-options]").forEach((options) => {
  const type = options.closest("form").querySelector('[name="field_type"]');
  const update = () => { options.hidden = type.value !== "dropdown"; };
  type.addEventListener("change", update);
  update();
});
