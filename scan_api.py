"""Versioned companion API. Owner-scoped writes and replay-safe item creation."""

import hashlib
import io
import json
import math
import re
import sqlite3
import uuid
import dropdown_fields
from datetime import date, datetime

from flask import Blueprint, g, jsonify, request, url_for
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.utils import secure_filename
from api_keys import authenticated


def migrate(db):
    db.execute("alter table items add column item_uuid text")
    db.execute("alter table items add column rfid_uid text")
    for row in db.execute("select id from items").fetchall():
        db.execute(
            "update items set item_uuid=? where id=?", (str(uuid.uuid4()), row["id"])
        )
    db.execute("create unique index items_uuid on items(item_uuid)")
    db.execute(
        "create unique index items_rfid on items(rfid_uid) where rfid_uid is not null"
    )
    db.execute("""create table scan_requests (
        user_id integer not null references users(id), request_id text not null,
        payload_hash text not null, item_id integer references items(id) on delete set null,
        primary key(user_id,request_id))""")


def normalize_uid(value):
    if not value:
        return None
    if not isinstance(value, str):
        raise ValueError("RFID UID must be text.")
    uid = value.strip().upper().replace(":", "").replace(" ", "").replace("-", "")
    if len(uid) not in (8, 14, 20) or not re.fullmatch("[0-9A-F]+", uid):
        raise ValueError("RFID UID must contain 4, 7, or 10 hexadecimal bytes.")
    return uid


def identity(form):
    raw = form.get("item_uuid", "").strip()
    return str(uuid.UUID(raw)) if raw else str(uuid.uuid4()), normalize_uid(
        form.get("rfid_uid")
    )


def field_value(field, value):
    kind = field["field_type"]
    if kind == "checkbox":
        if value not in (True, False, "true", "false", ""):
            raise ValueError(f"{field['name']}: expected a checkbox value.")
        return "true" if value in (True, "true") else "false"
    if not isinstance(value, str):
        raise ValueError(f"{field['name']}: expected text.")
    value = value.strip()
    if not value:
        return value
    if kind == "dropdown" and value not in field.get("options", []):
        raise ValueError(f"{field['name']}: choose one of the configured options.")
    if kind == "number" and not math.isfinite(float(value)):
        raise ValueError("Number must be finite.")
    if kind == "date":
        date.fromisoformat(value)
    if kind == "timestamp":
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    if kind == "url" and not re.match(r"^https?://[^\s]+$", value):
        raise ValueError("URL must start with http:// or https://.")
    return value


def collection_schema(host, collection):
    fields = [dict(f) for f in host.get_fields(collection["id"])]
    # Deployments can specify mandatory existing collection fields; never add silently.
    requirements = host.app.config.get("MINI_SCAN_REQUIRED_FIELDS", {})
    issues = []
    for name, kind in requirements.items():
        found = [f for f in fields if f["name"].casefold() == name.casefold()]
        if not found:
            issues.append(f'Add field "{name}" with type "{kind}".')
        elif len(found) > 1:
            issues.append(f'Keep exactly one field named "{name}".')
        elif found[0]["field_type"] != kind:
            issues.append(
                f'Change "{name}" to type "{kind}" (currently "{found[0]["field_type"]}").'
            )
    for field in fields:
        field["options"] = (
            dropdown_fields.options(field) if field["field_type"] == "dropdown" else []
        )
        field["required"] = field["name"].casefold() in {
            n.casefold() for n in requirements
        }
        if field["field_type"] not in host.FIELD_TYPES:
            issues.append(f'Change "{field["name"]}" to a supported field type.')
    return dict(
        id=collection["id"],
        name=collection["name"],
        description=collection["description"],
        fields=fields,
        issues=issues,
        edit_url=url_for("edit_collection", collection_id=collection["id"]),
        core_fields=[
            {
                "name": "item_uuid",
                "label": "UUID",
                "type": "uuid",
                "required": True,
                "auto_generated": True,
            },
            {"name": "name", "type": "text", "required": True},
            {"name": "description", "type": "multiline", "required": False},
            {"name": "tags", "type": "text", "required": False},
        ],
        identity={"uuid": True, "rfid": True},
    )


def register(host):
    bp = Blueprint("scan_api", __name__, url_prefix="/api/v1")
    auth = authenticated(host)

    @bp.after_request
    def no_cache(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @bp.errorhandler(400)
    @bp.errorhandler(413)
    def bad_request(error):
        return jsonify(error=error.description), error.code

    @bp.route("/me")
    @auth
    def me():
        return jsonify(
            id=g.user["id"], username=g.user["username"], role=g.user["role"]
        )

    @bp.route("/collections")
    @auth
    def collections():
        rows = (
            host.get_db()
            .execute(
                "select * from collections where owner_id=? order by name,id",
                (g.user["id"],),
            )
            .fetchall()
        )
        return jsonify(collections=[collection_schema(host, row) for row in rows])

    @bp.route("/items/by-rfid/<uid>")
    @auth
    def by_rfid(uid):
        try:
            uid = normalize_uid(uid)
        except ValueError as error:
            return jsonify(error=str(error)), 400
        row = (
            host.get_db()
            .execute(
                """select items.id,items.collection_id,items.name,items.item_uuid,items.rfid_uid
            from items join collections on collections.id=items.collection_id
            where items.rfid_uid=? and collections.owner_id=?""",
                (uid, g.user["id"]),
            )
            .fetchone()
        )
        if row is None:
            return jsonify(error="Tag not found."), 404
        return jsonify(item=dict(row), url=url_for("edit_item", item_id=row["id"]))

    @bp.route("/collections/<int:collection_id>/items", methods=["POST"])
    @auth
    def create(collection_id):
        db = host.get_db()
        collection = db.execute(
            "select * from collections where id=? and owner_id=?",
            (collection_id, g.user["id"]),
        ).fetchone()
        if collection is None:
            return jsonify(error="Collection not found."), 404
        if g.user["role"] not in ("collector", "admin"):
            return jsonify(error="This account cannot create items."), 403
        staged = []
        written = []
        try:
            payload = json.loads(request.form.get("metadata", "{}"))
            if not isinstance(payload, dict):
                raise ValueError("Metadata must be an object.")
            request_id = str(uuid.UUID(request.headers.get("Idempotency-Key", "")))
            item_uuid = str(uuid.UUID(payload.get("item_uuid", "")))
            uid = normalize_uid(payload.get("rfid_uid"))
            name = payload.get("name", "")
            description = payload.get("description", "")
            tags = payload.get("tags", "")
            if not all(isinstance(x, str) for x in (name, description, tags)):
                raise ValueError("Name, description and tags must be text.")
            if not name.strip() or len(name) > 500:
                raise ValueError("Name is required (up to 500 characters).")
            files = request.files.getlist("images")
            if not 1 <= len(files) <= 30:
                raise ValueError("Select between 1 and 30 finalized images.")
            fingerprint = hashlib.sha256(
                json.dumps([collection_id, payload], sort_keys=True).encode()
            )
            for upload in files:
                raw = upload.read()
                fingerprint.update(hashlib.sha256(raw).digest())
                with Image.open(io.BytesIO(raw)) as image:
                    if image.width * image.height > 40_000_000:
                        raise ValueError("Image exceeds 40 megapixels.")
                    image = ImageOps.exif_transpose(image).convert("RGB")
                    output = io.BytesIO()
                    image.save(output, format="JPEG", quality=95)
                staged.append(
                    (
                        secure_filename(upload.filename or "photo.jpg")[:200],
                        output.getvalue(),
                    )
                )
            digest = fingerprint.hexdigest()
            db.execute("begin immediate")
            previous = db.execute(
                "select * from scan_requests where user_id=? and request_id=?",
                (g.user["id"], request_id),
            ).fetchone()
            if previous:
                db.rollback()
                if previous["payload_hash"] != digest:
                    return jsonify(
                        error="This upload was already submitted with different data. Start a new submission.",
                        code="submission_changed",
                        url=url_for("item_detail", item_id=previous["item_id"]) if previous["item_id"] else None,
                    ), 409
                if previous["item_id"] is None:
                    return jsonify(
                        error="The previously uploaded item was deleted.", code="item_deleted",
                    ), 409
                return jsonify(
                    item_id=previous["item_id"],
                    replayed=True,
                    url=url_for("item_detail", item_id=previous["item_id"]),
                )
            schema = collection_schema(host, collection)
            if schema["issues"]:
                db.rollback()
                return jsonify(
                    error="Collection needs edits.",
                    issues=schema["issues"],
                    edit_url=schema["edit_url"],
                ), 422
            values = payload.get("values", {})
            if not isinstance(values, dict):
                raise ValueError("Field values must be an object.")
            if set(values) - {str(f["id"]) for f in schema["fields"]}:
                raise ValueError(
                    "Collection fields changed. Refresh the collection before uploading."
                )
            normalized = {}
            for field in schema["fields"]:
                value = field_value(field, values.get(str(field["id"]), ""))
                if field["required"] and not value:
                    raise ValueError(f"{field['name']} is required.")
                normalized[field["id"]] = value
            if db.execute(
                "select 1 from items where item_uuid=? or rfid_uid=?", (item_uuid, uid)
            ).fetchone():
                raise sqlite3.IntegrityError("Duplicate item identity")
            cursor = db.execute(
                "insert into items(collection_id,name,description,item_uuid,rfid_uid) values(?,?,?,?,?)",
                (collection_id, name.strip(), description.strip(), item_uuid, uid),
            )
            item_id = cursor.lastrowid
            for fid, value in normalized.items():
                db.execute(
                    "insert into item_values values(?,?,?)", (item_id, fid, value)
                )
            host.replace_item_tags(item_id, host.parse_tags(tags))
            for position, (original, data) in enumerate(staged):
                filename = uuid.uuid4().hex + ".jpg"
                path = host.UPLOAD_DIR / filename
                written.append(path)
                path.write_bytes(data)
                db.execute(
                    "insert into item_images(item_id,filename,original_name,position,gallery_position) values(?,?,?,?,?)",
                    (item_id, filename, original, position, position),
                )
            db.execute(
                "insert into scan_requests values(?,?,?,?)",
                (g.user["id"], request_id, digest, item_id),
            )
            db.commit()
            return jsonify(
                item_id=item_id,
                replayed=False,
                url=url_for("item_detail", item_id=item_id),
            ), 201
        except sqlite3.IntegrityError:
            db.rollback()
            for path in written:
                path.unlink(missing_ok=True)
            return jsonify(
                error="That RFID tag or UUID is already associated with an item. Update its identity in Menagerie.",
                code="identity_conflict",
            ), 409
        except (
            ValueError,
            TypeError,
            AttributeError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
        ) as error:
            db.rollback()
            for path in written:
                path.unlink(missing_ok=True)
            return jsonify(error=str(error)), 422
        except Exception:
            db.rollback()
            for path in written:
                path.unlink(missing_ok=True)
            raise

    @host.app.before_request
    def validate_web_identity():
        if request.method != "POST" or request.endpoint not in (
            "new_item",
            "edit_item",
        ):
            return
        if g.user is None:
            return
        try:
            item_uuid, uid = identity(request.form)
            current = (request.view_args or {}).get("item_id", -1)
            conflict = (
                host.get_db()
                .execute(
                    "select id from items where id != ? and (item_uuid=? or (rfid_uid=? and rfid_uid is not null))",
                    (current, item_uuid, uid),
                )
                .fetchone()
            )
            if conflict:
                raise ValueError(
                    "This UUID or RFID tag is already assigned to another item."
                )
        except (ValueError, TypeError, AttributeError) as error:
            host.flash(str(error), "error")
            return host.redirect(request.path)

    host.app.register_blueprint(bp)
