# Menagerie UI revamp review

Branch: `ui/menagerie-revamp`, based on `5c16ddb` on `main`.

The refresh uses Linear's navigation hierarchy, Airtable's collection controls, and museum-style object browsing as references. All four Menagerie palettes retain their existing color values. The implementation uses local CSS, native HTML controls, and small JavaScript interactions; it adds no application dependencies. Image-specific random-gallery ratios add two columns to image records automatically on startup, both defaulting to 1. Optional focal-point coordinates are also added; existing framing remains unchanged until a focal point is chosen.

## What changed

- Persistent desktop sidebar with Explore, Workspace, Tools, and account controls. Smaller screens use a Browse button; Escape closes navigation and restores focus. Links reflect the current section and existing account permissions.
- One collection toolbar for search, sorting, ordering, and table/gallery switching. Switching views submits the current filters rather than discarding them. Additional filters expand when active, with a result count and a Clear filters action.
- Collection cover images and item counts, image thumbnails in tables, and quieter gallery cards. Dropdown option management is available in an expandable section.
- Each photograph has its own random-gallery width × height ratio, configured on its Frame image page. Defaults are 1 × 1; the site-wide ratio control has been removed. The random gallery shows only the first gallery-enabled photograph per item, in saved gallery order, using that photograph’s ratio. Personal galleries retain saved item layouts and slideshows.
- A large item photograph with selectable thumbnails and metadata beside it, stacking on phones. Image management expands below the photographs; the existing framing, gallery selection, autosave, and drag ordering remain available. Move earlier/later buttons support keyboard and touch ordering.
- Frame image includes an original photograph where clicking or tapping selects a detail to center, with zoom and horizontal/vertical focus sliders. Focal points save per photograph and stay centered across tile sizes; framing clamps at image edges. Reset clears the focal point.
- The item editor shows existing photographs with gallery selection, saved ratio, ordering controls, and deletion. Image changes save immediately, separately from item details. Framing opens in a new tab and its saved settings refresh when returning to the editor.
- Tag chips offer known-tag autocomplete, keyboard selection, removal, and new tags. Suggestions come from the collection owner’s items; other collectors’ private tags are excluded. Enter/comma adds tags, arrow keys navigate suggestions, and text still being typed is included when saving.
- Item forms grouped into Overview, Collection details, Identification, and Photographs. Identification expands on demand; its values still submit when collapsed. Collection and settings forms use similarly clear sections. Editing forms include Cancel actions.
- Consistent typography, restrained borders, rounded controls, focus indicators, responsive layouts, and reduced-motion support. Theme color variables remain in `static/style.css`; the refresh is in `static/revamp.css`.

## Inspect locally

The branch is checked out and committed. Start Menagerie using your usual environment and launch command from the README. If a development server is already running without automatic reload, restart it to pick up the changed templates.

```sh
git switch ui/menagerie-revamp
.venv/bin/python app.py
```

Open your normal local server URL (the default is `http://localhost:8000`).

Useful pages to review:

1. Gallery, User galleries, and My gallery: photographs, captions, slideshow controls, and saved layout editing.
2. My collections: covers, counts, and empty states.
3. A populated collection: search/filter/sort, switch views with filters applied, and clear filters.
4. An item with several photographs: thumbnail selection, metadata, gallery toggles, framing links, and image ordering. Open Frame image to set independent width and height values and check the live preview. Click a face in the original photograph, zoom in, and verify that the chosen detail remains centered in the gallery.
5. New/edit collection and new/edit item: contextual field controls, collapsed identification, save and cancel.
6. Settings and Administration: section grouping and navigation permissions.
7. Repeat on a narrow screen and in Classic dark/light and Museum dark/light themes.

## Validation

Automated tests cover the existing application plus ownership of collection covers, public image visibility, role-based navigation, and preservation of UUID/RFID values when editing an overview.

```sh
.venv/bin/python -m unittest discover -s tests
```

Chromium checks used isolated temporary sample data at desktop (1440 px) and phone (390 px) widths. They exercised all four themes, view switches retaining search/filter/sort values, photograph selection, image toggle autosave, button-based image reordering, dropdown options and field removal, mobile navigation, form validation and submission, tag autocomplete and keyboard interactions, photo deletion without losing unsaved details, framing refresh after returning from another tab, and public permissions. No JavaScript errors or page-level horizontal overflow were observed in those flows. Wide collection tables scroll inside their container.

The branch has not been merged into `main`.
