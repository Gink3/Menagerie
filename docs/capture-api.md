# Capture API compatibility

Mini Scan uses the bearer-authenticated `/api/v1` HTTP endpoints implemented in
`scan_api.py`. Desktop runtime code depends on the HTTP contract, not Flask,
SQLite, or imports from the web application.

- Connect: `GET /me` and `GET /collections`.
- Resolve a tag: `GET /items/by-rfid/<uid>`.
- Create an item: `POST /collections/<id>/items`.
- Append/replace photos: `POST /items/<id>/images` with `image_mode`.
- Ensure standardized Advanced fields: `POST /collections/<id>/capture-fields`.
- Retrieve per-photo acquisition records: `GET /items/<id>/captures`.

Uploads contain multipart `images` in selected order and a JSON `metadata` form
field. The optional `captures` array follows the same order, with `null` for
imported photos. Version 1 records contain acquisition time, camera, native size,
optional lighting settings and focus measurement. Menagerie creates canonical
Advanced fields transactionally and derives summaries from remaining photos.

`Idempotency-Key` identifies a frozen submission. Retrying unchanged data returns
the existing item; changing a previously used submission yields a conflict.
Identity conflicts retain local drafts so the user can resolve them. Existing
collections and uploads without acquisition metadata remain supported.

The tests in `tests/integration/` exercise the desktop client and draft format
against a real local HTTP server backed by a temporary Menagerie database. They
check ordered capture records, schema creation, retry behavior, and append/replace.
See [setup](capture-setup.md) for commands. CI runs web, desktop and integration
checks separately; web-only testing skips integration tests when desktop
packages are unavailable.
