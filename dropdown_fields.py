"""Collection dropdown definitions, validation and owner-managed option additions."""

import json
from flask import abort, flash, redirect, request, url_for


def migrate(db):
    db.execute("alter table fields add column options_json text not null default '[]'")


def options(field):
    return json.loads(field["options_json"])


def parse(raw):
    result = []
    for value in raw.splitlines():
        value = value.strip()
        if not value:
            continue
        if len(value) > 200:
            abort(400, description="Dropdown options must be at most 200 characters.")
        if value not in result:
            result.append(value)
    if len(result) > 200:
        abort(400, description="A dropdown supports up to 200 options.")
    return result


def encode(raw, kind, db=None, field_id=None):
    values = parse(raw) if kind == "dropdown" else []
    if kind == "dropdown":
        if not values:
            abort(400, description="Define at least one option for a dropdown field.")
        if db is not None and field_id:
            used = {
                row["value"]
                for row in db.execute(
                    "select distinct value from item_values where field_id=? and value != ?",
                    (field_id, ""),
                )
            }
            if used - set(values):
                abort(
                    400,
                    description="Options used by existing items cannot be removed. Include those values in the dropdown options first.",
                )
    return json.dumps(values)


def specs(host, form):
    rows = host.form_collection_field_rows(form)
    names = form.getlist("field_name")
    submitted = form.getlist("field_options")
    aligned = (
        [
            submitted[i] if i < len(submitted) else ""
            for i, name in enumerate(names)
            if name.strip()
        ]
        if names
        else [""] * len(rows)
    )
    return [
        (fid, name, kind, encode(raw, kind, host.get_db(), fid))
        for (fid, name, kind), raw in zip(rows, aligned)
    ]


def register(host):
    host.app.add_template_filter(options, "dropdown_options")

    @host.app.route(
        "/collections/<int:collection_id>/fields/<int:field_id>/options",
        methods=["POST"],
    )
    @host.collection_manager_required
    def add_field_option(collection, field_id):
        db = host.get_db()
        db.execute("begin immediate")
        field = db.execute(
            "select * from fields where id=? and collection_id=?",
            (field_id, collection["id"]),
        ).fetchone()
        if field is None:
            abort(404)
        if field["field_type"] != "dropdown":
            abort(400, description="This field is not a dropdown.")
        value = request.form.get("option", "").strip()
        if not value or "\n" in value or "\r" in value:
            abort(400, description="Enter one nonempty option.")
        current = options(field)
        if value not in current:
            encoded = encode("\n".join(current + [value]), "dropdown")
            db.execute(
                "update fields set options_json=? where id=?", (encoded, field_id)
            )
        db.commit()
        flash("Dropdown option saved.", "success")
        return redirect(url_for("collection_view", collection_id=collection["id"]))
