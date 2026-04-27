import os
import secrets
import sqlite3
import time
import uuid
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

from flask import (
    Flask,
    abort,
    jsonify,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
ASSET_DIR = BASE_DIR / "assets"
DATA_DIR = Path(os.environ.get("MENAGERIE_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DATABASE = DATA_DIR / "menagerie.sqlite"
ALLOWED_IMAGE_EXTENSIONS = {"avif", "gif", "jpeg", "jpg", "png", "webp"}
ROLES = {"admin", "collector", "viewer"}
FIELD_TYPE_CHOICES = (
    ("text", "Single line text"),
    ("multiline", "Multi-line text"),
    ("number", "Number"),
    ("date", "Date"),
    ("timestamp", "Timestamp"),
    ("url", "URL"),
    ("checkbox", "Checkbox"),
)
FIELD_TYPES = {value for value, label in FIELD_TYPE_CHOICES}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("MENAGERIE_SECRET_KEY", "dev-change-me")
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = os.environ.get("MENAGERIE_SESSION_COOKIE_SAMESITE", "Lax")
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("MENAGERIE_SESSION_COOKIE_SECURE", "0") == "1"
if os.environ.get("MENAGERIE_PROXY_FIX", "1") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

RATE_LIMITS = {}


def get_db():
    if "db" not in g:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("pragma foreign_keys = on")
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(
        """
        create table if not exists users (
            id integer primary key autoincrement,
            username text not null unique,
            password_hash text not null,
            role text not null default 'collector',
            created_at text not null default current_timestamp
        );

        create table if not exists user_applications (
            id integer primary key autoincrement,
            username text not null unique,
            password_hash text not null,
            created_at text not null default current_timestamp
        );

        create table if not exists collections (
            id integer primary key autoincrement,
            name text not null,
            description text not null default '',
            owner_id integer references users(id),
            created_at text not null default current_timestamp
        );

        create table if not exists fields (
            id integer primary key autoincrement,
            collection_id integer not null references collections(id) on delete cascade,
            name text not null,
            field_type text not null default 'text',
            position integer not null default 0
        );

        create table if not exists items (
            id integer primary key autoincrement,
            collection_id integer not null references collections(id) on delete cascade,
            name text not null,
            description text not null default '',
            gallery_x integer not null default 0,
            gallery_y integer not null default 0,
            gallery_w integer not null default 2,
            gallery_h integer not null default 2,
            created_at text not null default current_timestamp,
            updated_at text not null default current_timestamp
        );

        create table if not exists item_values (
            item_id integer not null references items(id) on delete cascade,
            field_id integer not null references fields(id) on delete cascade,
            value text not null default '',
            primary key (item_id, field_id)
        );

        create table if not exists item_images (
            id integer primary key autoincrement,
            item_id integer not null references items(id) on delete cascade,
            filename text not null,
            original_name text not null,
            position integer not null default 0,
            gallery_position integer not null default 0,
            gallery_enabled integer not null default 1,
            gallery_x integer not null default 0,
            gallery_y integer not null default 0,
            gallery_w integer not null default 2,
            gallery_h integer not null default 2,
            crop_x real not null default 50,
            crop_y real not null default 50,
            crop_zoom real not null default 1,
            created_at text not null default current_timestamp
        );

        create table if not exists tags (
            id integer primary key autoincrement,
            name text not null unique
        );

        create table if not exists item_tags (
            item_id integer not null references items(id) on delete cascade,
            tag_id integer not null references tags(id) on delete cascade,
            primary key (item_id, tag_id)
        );

        create table if not exists settings (
            key text primary key,
            value text not null
        );
        """
    )
    columns = [row["name"] for row in db.execute("pragma table_info(collections)").fetchall()]
    if "owner_id" not in columns:
        db.execute("alter table collections add column owner_id integer references users(id)")
    field_columns = [row["name"] for row in db.execute("pragma table_info(fields)").fetchall()]
    if "field_type" not in field_columns:
        db.execute("alter table fields add column field_type text not null default 'text'")
    item_columns = [row["name"] for row in db.execute("pragma table_info(items)").fetchall()]
    item_defaults = {
        "gallery_x": "integer not null default 0",
        "gallery_y": "integer not null default 0",
        "gallery_w": "integer not null default 2",
        "gallery_h": "integer not null default 2",
    }
    for column, definition in item_defaults.items():
        if column not in item_columns:
            db.execute(f"alter table items add column {column} {definition}")
    image_columns = [row["name"] for row in db.execute("pragma table_info(item_images)").fetchall()]
    image_defaults = {
        "gallery_position": "integer not null default 0",
        "gallery_enabled": "integer not null default 1",
        "gallery_x": "integer not null default 0",
        "gallery_y": "integer not null default 0",
        "gallery_w": "integer not null default 2",
        "gallery_h": "integer not null default 2",
        "crop_x": "real not null default 50",
        "crop_y": "real not null default 50",
        "crop_zoom": "real not null default 1",
    }
    for column, definition in image_defaults.items():
        if column not in image_columns:
            db.execute(f"alter table item_images add column {column} {definition}")
    db.execute("update users set role = 'collector' where role = 'user'")
    admin_count = db.execute("select count(*) from users where role = 'admin'").fetchone()[0]
    if admin_count == 0:
        username = os.environ.get("MENAGERIE_ADMIN_USERNAME", "admin")
        password = os.environ.get("MENAGERIE_ADMIN_PASSWORD", "admin123")
        if (
            os.environ.get("MENAGERIE_ALLOW_INSECURE_DEFAULTS") != "1"
            and username == "admin"
            and password == "admin123"
        ):
            raise RuntimeError("Set MENAGERIE_ADMIN_USERNAME and MENAGERIE_ADMIN_PASSWORD before first startup.")
        db.execute(
            "insert into users (username, password_hash, role) values (?, ?, 'admin')",
            (username, generate_password_hash(password)),
        )
    first_admin = db.execute("select id from users where role = 'admin' order by id limit 1").fetchone()
    if first_admin:
        db.execute("update collections set owner_id = ? where owner_id is null", (first_admin["id"],))
    layout_seeded = db.execute("select value from settings where key = 'gallery_layout_seeded'").fetchone()
    if layout_seeded is None:
        owner_positions = {}
        images = db.execute(
            """
            select item_images.id, collections.owner_id
            from item_images
            join items on items.id = item_images.item_id
            join collections on collections.id = items.collection_id
            order by collections.owner_id, item_images.position, item_images.id
            """
        ).fetchall()
        for image in images:
            position = owner_positions.get(image["owner_id"], 0)
            db.execute(
                "update item_images set gallery_x = ?, gallery_y = ? where id = ?",
                ((position * 2) % 6, (position * 2) // 6, image["id"]),
            )
            owner_positions[image["owner_id"]] = position + 1
        db.execute("insert into settings (key, value) values ('gallery_layout_seeded', '1')")
    item_layout_seeded = db.execute("select value from settings where key = 'gallery_item_layout_seeded'").fetchone()
    if item_layout_seeded is None:
        owner_positions = {}
        items = db.execute(
            """
            select distinct items.id, collections.owner_id
            from items
            join collections on collections.id = items.collection_id
            join item_images on item_images.item_id = items.id and item_images.gallery_enabled = 1
            order by collections.owner_id, items.id
            """
        ).fetchall()
        for item in items:
            position = owner_positions.get(item["owner_id"], 0)
            db.execute(
                "update items set gallery_x = ?, gallery_y = ? where id = ?",
                ((position * 2) % 6, (position * 2) // 6, item["id"]),
            )
            owner_positions[item["owner_id"]] = position + 1
        db.execute("insert into settings (key, value) values ('gallery_item_layout_seeded', '1')")
    db.commit()


def enforce_secure_config():
    if os.environ.get("MENAGERIE_ALLOW_INSECURE_DEFAULTS") == "1":
        return
    if app.config["SECRET_KEY"] == "dev-change-me":
        raise RuntimeError("Set MENAGERIE_SECRET_KEY before running Menagerie publicly.")


@app.before_request
def load_current_user():
    enforce_secure_config()
    init_db()
    user_id = session.get("user_id")
    g.user = None
    if user_id:
        g.user = get_db().execute("select * from users where id = ?", (user_id,)).fetchone()
    if request.method == "POST" and not valid_csrf_token(request.form.get("csrf_token") or request.headers.get("X-CSRFToken")):
        abort(400)


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        return view(**kwargs)

    return wrapped_view


def admin_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        if g.user["role"] != "admin":
            abort(403)
        return view(**kwargs)

    return wrapped_view


def normalize_role(role):
    return role if role in ROLES else "collector"


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def valid_csrf_token(token):
    return bool(token and session.get("csrf_token") and secrets.compare_digest(token, session["csrf_token"]))


@app.context_processor
def inject_security_helpers():
    return {"csrf_token": csrf_token, "csrf_field": csrf_field}


def csrf_field():
    return f'<input type="hidden" name="csrf_token" value="{csrf_token()}">'


def local_redirect_target(target, fallback):
    if not target:
        return fallback
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith("/"):
        return fallback
    return target


def rate_limit(key, limit, window_seconds):
    now = time.time()
    attempts = [timestamp for timestamp in RATE_LIMITS.get(key, []) if now - timestamp < window_seconds]
    if len(attempts) >= limit:
        RATE_LIMITS[key] = attempts
        return False
    attempts.append(now)
    RATE_LIMITS[key] = attempts
    return True


def username_exists(username):
    return (
        get_db().execute("select 1 from users where lower(username) = lower(?)", (username,)).fetchone()
        is not None
    )


def application_exists(username):
    return (
        get_db()
        .execute("select 1 from user_applications where lower(username) = lower(?)", (username,))
        .fetchone()
        is not None
    )


def is_background_request():
    return request.headers.get("X-Requested-With") == "fetch"


def user_can_create_collection():
    return g.user is not None and g.user["role"] in {"admin", "collector"}


def user_can_manage_collection(collection):
    if g.user is None:
        return False
    if g.user["role"] == "admin":
        return True
    return g.user["role"] == "collector" and collection["owner_id"] == g.user["id"]


def user_can_view_collection(collection):
    if g.user is None:
        return False
    if g.user["role"] == "admin":
        return True
    return collection["owner_id"] == g.user["id"]


def user_can_manage_image(image):
    if g.user is None:
        return False
    if g.user["role"] == "admin":
        return True
    return g.user["role"] == "collector" and image["owner_id"] == g.user["id"]


def image_is_public(image):
    return bool(image["gallery_enabled"])


def item_is_public(item_id):
    return (
        get_db()
        .execute("select 1 from item_images where item_id = ? and gallery_enabled = 1", (item_id,))
        .fetchone()
        is not None
    )


def user_can_view_image(image):
    return image_is_public(image) or user_can_manage_image(image)


def valid_collection_owner_id(owner_id, fallback_id):
    try:
        owner_id = int(owner_id or fallback_id)
    except (TypeError, ValueError):
        abort(400)
    owner = get_db().execute(
        "select id from users where id = ? and role in ('admin', 'collector')",
        (owner_id,),
    ).fetchone()
    if owner is None:
        abort(400)
    return owner["id"]


def collection_manager_required(view):
    @wraps(view)
    def wrapped_view(collection_id, **kwargs):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        collection = get_collection(collection_id)
        if not user_can_manage_collection(collection):
            abort(403)
        return view(collection, **kwargs)

    return wrapped_view


def row_to_dict(row):
    return dict(row) if row else None


def get_collection(collection_id):
    collection = get_db().execute(
        """
        select collections.*, users.username as owner_username
        from collections
        left join users on users.id = collections.owner_id
        where collections.id = ?
        """,
        (collection_id,),
    ).fetchone()
    if collection is None:
        abort(404)
    return collection


def get_user(user_id):
    user = get_db().execute("select id, username, role, created_at from users where id = ?", (user_id,)).fetchone()
    if user is None:
        abort(404)
    return user


def get_item(item_id):
    item = get_db().execute("select * from items where id = ?", (item_id,)).fetchone()
    if item is None:
        abort(404)
    return item


def get_field(field_id):
    field = get_db().execute("select * from fields where id = ?", (field_id,)).fetchone()
    if field is None:
        abort(404)
    return field


def get_image(image_id):
    image = get_db().execute(
        """
        select item_images.*, items.name as item_name, items.description as item_description,
            items.collection_id, collections.name as collection_name,
            collections.owner_id, users.username as owner_username
        from item_images
        join items on items.id = item_images.item_id
        join collections on collections.id = items.collection_id
        left join users on users.id = collections.owner_id
        where item_images.id = ?
        """,
        (image_id,),
    ).fetchone()
    if image is None:
        abort(404)
    return image


def gallery_images(owner_id=None, mixed=False):
    where = ["item_images.gallery_enabled = 1"]
    params = []
    if owner_id is not None:
        where.append("collections.owner_id = ?")
        params.append(owner_id)
    order = "users.username, items.gallery_y, items.gallery_x, item_images.gallery_position, item_images.position, item_images.id"
    if mixed:
        order = "items.updated_at desc, item_images.gallery_position, item_images.position, item_images.id"
    rows = get_db().execute(
        f"""
        select item_images.*, items.id as item_id, items.name as item_name,
            items.gallery_x as item_gallery_x, items.gallery_y as item_gallery_y,
            items.gallery_w as item_gallery_w, items.gallery_h as item_gallery_h,
            collections.id as collection_id, collections.name as collection_name,
            collections.owner_id, users.username as owner_username
        from item_images
        join items on items.id = item_images.item_id
        join collections on collections.id = items.collection_id
        left join users on users.id = collections.owner_id
        where {" and ".join(where)}
        order by {order}
        """,
        params,
    ).fetchall()
    gallery_items = []
    item_lookup = {}
    for row in rows:
        item_id = row["item_id"]
        if item_id not in item_lookup:
            item = {
                "id": item_id,
                "name": row["item_name"],
                "collection_id": row["collection_id"],
                "collection_name": row["collection_name"],
                "owner_id": row["owner_id"],
                "owner_username": row["owner_username"],
                "gallery_x": row["item_gallery_x"],
                "gallery_y": row["item_gallery_y"],
                "gallery_w": row["item_gallery_w"],
                "gallery_h": row["item_gallery_h"],
                "images": [],
            }
            item_lookup[item_id] = item
            gallery_items.append(item)
        item_lookup[item_id]["images"].append(row)
    return normalize_gallery_layout(gallery_items)


def gallery_users():
    return get_db().execute(
        """
        select users.id, users.username, count(distinct items.id) as item_count,
            count(item_images.id) as image_count, min(item_images.filename) as preview_filename
        from users
        join collections on collections.owner_id = users.id
        join items on items.collection_id = collections.id
        join item_images on item_images.item_id = items.id and item_images.gallery_enabled = 1
        group by users.id, users.username
        order by lower(users.username)
        """
    ).fetchall()


def normalize_gallery_layout(items):
    normalized = []
    occupied = set()
    for position, item in enumerate(items):
        tile = row_to_dict(item)
        width = max(1, min(6, tile["gallery_w"]))
        height = max(1, min(6, tile["gallery_h"]))
        x = max(0, min(6 - width, tile["gallery_x"]))
        y = max(0, tile["gallery_y"])
        if position and tile_overlaps(occupied, x, y, width, height):
            x, y = next_gallery_slot(occupied, width, height)
        occupy_gallery_slot(occupied, x, y, width, height)
        tile["gallery_x"] = x
        tile["gallery_y"] = y
        tile["gallery_w"] = width
        tile["gallery_h"] = height
        normalized.append(tile)
    return normalized


def tile_overlaps(occupied, x, y, width, height):
    return any((cell_x, cell_y) in occupied for cell_x in range(x, x + width) for cell_y in range(y, y + height))


def occupy_gallery_slot(occupied, x, y, width, height):
    for cell_x in range(x, x + width):
        for cell_y in range(y, y + height):
            occupied.add((cell_x, cell_y))


def next_gallery_slot(occupied, width, height):
    for y in range(1000):
        for x in range(0, 7 - width):
            if not tile_overlaps(occupied, x, y, width, height):
                return x, y
    return 0, 0


def get_fields(collection_id):
    return get_db().execute(
        "select * from fields where collection_id = ? order by position, id", (collection_id,)
    ).fetchall()


def normalize_field_type(field_type):
    normalized = (field_type or "text").strip().lower().replace("-", " ").replace("_", " ")
    aliases = {
        "single line": "text",
        "single line text": "text",
        "multi line": "multiline",
        "multi line text": "multiline",
        "multiline text": "multiline",
        "datetime": "timestamp",
        "date time": "timestamp",
        "iso timestamp": "timestamp",
        "boolean": "checkbox",
        "true false": "checkbox",
    }
    normalized = aliases.get(normalized, normalized.replace(" ", ""))
    return normalized if normalized in FIELD_TYPES else "text"


def parse_field_definition(line):
    name, separator, raw_type = line.partition("|")
    name = name.strip()
    return name, normalize_field_type(raw_type if separator else "text")


def field_form_value(field, form):
    if field["field_type"] == "checkbox":
        return "true" if form.get(f"field_{field['id']}") else "false"
    return form.get(f"field_{field['id']}", "").strip()


@app.template_filter("display_field_value")
def display_field_value(value, field_type):
    if field_type == "checkbox":
        return "Yes" if value == "true" else "No"
    return value or ""


def value_matches_range(value, minimum=None, maximum=None, field_type="text"):
    if value == "":
        return False
    if field_type == "number":
        try:
            value = float(value)
            minimum = float(minimum) if minimum else None
            maximum = float(maximum) if maximum else None
        except (TypeError, ValueError):
            return False
    if minimum is not None and minimum != "" and value < minimum:
        return False
    if maximum is not None and maximum != "" and value > maximum:
        return False
    return True


def get_field_suggestions(collection_id):
    suggestions = {}
    rows = get_db().execute(
        """
        select fields.id as field_id, item_values.value
        from item_values
        join fields on fields.id = item_values.field_id
        where fields.collection_id = ? and trim(item_values.value) != ''
        group by fields.id, item_values.value
        order by lower(item_values.value)
        """,
        (collection_id,),
    ).fetchall()
    for row in rows:
        suggestions.setdefault(row["field_id"], []).append(row["value"])
    return suggestions


def parse_tags(raw_tags):
    seen = set()
    tags = []
    for tag in raw_tags.replace("\n", ",").split(","):
        cleaned = tag.strip().lower()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            tags.append(cleaned)
    return tags


def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def save_uploaded_images(item_id, files):
    db = get_db()
    current_position = db.execute(
        "select coalesce(max(position), -1) from item_images where item_id = ?", (item_id,)
    ).fetchone()[0]
    for image in files:
        if not image or image.filename == "":
            continue
        if not allowed_image(image.filename):
            flash(f"Skipped unsupported image: {image.filename}", "warning")
            continue
        original_name = secure_filename(image.filename)
        extension = original_name.rsplit(".", 1)[1].lower()
        filename = f"{uuid.uuid4().hex}.{extension}"
        image.save(UPLOAD_DIR / filename)
        current_position += 1
        db.execute(
            """
            insert into item_images (item_id, filename, original_name, position, gallery_position)
            values (?, ?, ?, ?, ?)
            """,
            (item_id, filename, original_name, current_position, current_position),
        )


def replace_item_tags(item_id, tags):
    db = get_db()
    db.execute("delete from item_tags where item_id = ?", (item_id,))
    for name in tags:
        db.execute("insert or ignore into tags (name) values (?)", (name,))
        tag_id = db.execute("select id from tags where name = ?", (name,)).fetchone()["id"]
        db.execute("insert into item_tags (item_id, tag_id) values (?, ?)", (item_id, tag_id))


def get_item_tags(item_id):
    return [
        row["name"]
        for row in get_db()
        .execute(
            """
            select tags.name from tags
            join item_tags on item_tags.tag_id = tags.id
            where item_tags.item_id = ?
            order by tags.name
            """,
            (item_id,),
        )
        .fetchall()
    ]


def collection_items(collection_id):
    db = get_db()
    items = [row_to_dict(row) for row in db.execute("select * from items where collection_id = ?", (collection_id,))]
    field_rows = db.execute(
        """
        select item_values.item_id, fields.name, item_values.value
        from item_values
        join fields on fields.id = item_values.field_id
        where fields.collection_id = ?
        """,
        (collection_id,),
    ).fetchall()
    images = db.execute(
        """
        select item_id, filename, gallery_enabled from item_images
        where item_id in (select id from items where collection_id = ?)
        order by position, id
        """,
        (collection_id,),
    ).fetchall()
    tag_rows = db.execute(
        """
        select item_tags.item_id, tags.name from item_tags
        join tags on tags.id = item_tags.tag_id
        where item_tags.item_id in (select id from items where collection_id = ?)
        order by tags.name
        """,
        (collection_id,),
    ).fetchall()

    for item in items:
        item["fields"] = {}
        item["tags"] = []
        item["has_images"] = False
        item["gallery_enabled"] = False
        item["primary_image"] = None
    for row in field_rows:
        next(item for item in items if item["id"] == row["item_id"])["fields"][row["name"]] = row["value"]
    for row in tag_rows:
        next(item for item in items if item["id"] == row["item_id"])["tags"].append(row["name"])
    for row in images:
        item = next(item for item in items if item["id"] == row["item_id"])
        item["has_images"] = True
        item["gallery_enabled"] = item["gallery_enabled"] or bool(row["gallery_enabled"])
        if item["primary_image"] is None:
            item["primary_image"] = row["filename"]
    return items


@app.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        rate_key = ("login", request.remote_addr or "unknown")
        if not rate_limit(rate_key, 8, 15 * 60):
            flash("Too many login attempts. Try again later.", "error")
            return render_template("login.html"), 429
        username = request.form["username"].strip()
        password = request.form["password"]
        user = get_db().execute("select * from users where username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            return redirect(local_redirect_target(request.args.get("next"), url_for("collections_index")))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/apply", methods=("GET", "POST"))
def apply():
    if request.method == "POST":
        rate_key = ("apply", request.remote_addr or "unknown")
        if not rate_limit(rate_key, 5, 60 * 60):
            flash("Too many applications from this address. Try again later.", "error")
            return render_template("apply.html"), 429
        username = request.form["username"].strip()
        password = request.form["password"]
        confirm_password = request.form.get("confirm_password", "")
        if not username:
            flash("Username is required.", "error")
        elif len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif password != confirm_password:
            flash("Passwords do not match.", "error")
        elif username_exists(username) or application_exists(username):
            flash("That username is already taken or awaiting approval.", "error")
        else:
            get_db().execute(
                "insert into user_applications (username, password_hash) values (?, ?)",
                (username, generate_password_hash(password)),
            )
            get_db().commit()
            flash("Application submitted. An admin will review it before you can sign in.", "success")
            return redirect(url_for("login"))
    return render_template("apply.html")


@app.route("/logout", methods=("POST",))
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    return render_template("gallery.html", images=gallery_images(mixed=True), owner=None, editable=False)


@app.route("/collections")
@login_required
def collections_index():
    collections = get_db().execute(
        """
        select collections.*, users.username as owner_username
        from collections
        left join users on users.id = collections.owner_id
        where collections.owner_id = ?
        order by collections.name
        """,
        (g.user["id"],),
    ).fetchall()
    return render_template("index.html", collections=collections, can_create=user_can_create_collection())


@app.route("/settings", methods=("GET", "POST"))
@login_required
def user_settings():
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        user = get_db().execute("select * from users where id = ?", (g.user["id"],)).fetchone()
        if not check_password_hash(user["password_hash"], current_password):
            flash("Current password is incorrect.", "error")
        elif len(new_password) < 8:
            flash("New password must be at least 8 characters.", "error")
        elif new_password != confirm_password:
            flash("New passwords do not match.", "error")
        else:
            get_db().execute(
                "update users set password_hash = ? where id = ?",
                (generate_password_hash(new_password), g.user["id"]),
            )
            get_db().commit()
            flash("Password updated.", "success")
            return redirect(url_for("user_settings"))
    return render_template("user_settings.html")


@app.route("/gallery/me")
@login_required
def my_gallery():
    return redirect(url_for("user_gallery", user_id=g.user["id"]))


@app.route("/galleries")
def user_galleries():
    return render_template("user_galleries.html", users=gallery_users())


@app.route("/users/<int:user_id>/gallery")
def user_gallery(user_id):
    owner = get_user(user_id)
    editable = g.user is not None and (g.user["role"] == "admin" or g.user["id"] == owner["id"])
    return render_template("gallery.html", images=gallery_images(owner["id"]), owner=owner, editable=editable)


@app.route("/gallery/images/<int:image_id>")
def gallery_image_detail(image_id):
    image = get_image(image_id)
    if not user_can_view_image(image):
        abort(404)
    return render_template("gallery_image.html", image=image)


@app.route("/gallery/layout", methods=("POST",))
@login_required
def save_gallery_layout():
    db = get_db()
    updates = request.get_json(silent=True) or []
    for update in updates:
        item = get_item(update.get("id"))
        collection = get_collection(item["collection_id"])
        if not user_can_manage_collection(collection):
            abort(403)
        x = max(0, min(5, int(update.get("x", item["gallery_x"]))))
        y = max(0, int(update.get("y", item["gallery_y"])))
        w = max(1, min(6, int(update.get("w", item["gallery_w"]))))
        h = max(1, min(6, int(update.get("h", item["gallery_h"]))))
        if x + w > 6:
            x = 6 - w
        db.execute(
            "update items set gallery_x = ?, gallery_y = ?, gallery_w = ?, gallery_h = ? where id = ?",
            (x, y, w, h, item["id"]),
        )
    db.commit()
    return jsonify({"ok": True})


@app.route("/images/<int:image_id>/settings", methods=("GET", "POST"))
@login_required
def image_settings(image_id):
    image = get_image(image_id)
    if not user_can_manage_image(image):
        abort(403)
    if request.method == "POST":
        crop_x = max(0, min(100, float(request.form.get("crop_x", image["crop_x"]))))
        crop_y = max(0, min(100, float(request.form.get("crop_y", image["crop_y"]))))
        crop_zoom = max(1, min(3, float(request.form.get("crop_zoom", image["crop_zoom"]))))
        get_db().execute(
            "update item_images set crop_x = ?, crop_y = ?, crop_zoom = ? where id = ?",
            (crop_x, crop_y, crop_zoom, image["id"]),
        )
        get_db().commit()
        if is_background_request():
            return jsonify({"ok": True})
        flash("Image settings saved.", "success")
        return redirect(url_for("item_detail", item_id=image["item_id"]))
    return render_template("image_crop.html", image=image)


@app.route("/images/<int:image_id>/delete", methods=("POST",))
@login_required
def delete_image(image_id):
    image = get_image(image_id)
    if not user_can_manage_image(image):
        abort(403)
    get_db().execute("delete from item_images where id = ?", (image["id"],))
    get_db().commit()
    try:
        (UPLOAD_DIR / image["filename"]).unlink()
    except FileNotFoundError:
        pass
    flash("Image deleted.", "success")
    return redirect(url_for("item_detail", item_id=image["item_id"]))


@app.route("/images/<int:image_id>/gallery", methods=("POST",))
@login_required
def update_image_gallery(image_id):
    image = get_image(image_id)
    if not user_can_manage_image(image):
        abort(403)
    gallery_enabled = 1 if request.form.get("gallery_enabled") == "on" else 0
    get_db().execute(
        "update item_images set gallery_enabled = ? where id = ?",
        (gallery_enabled, image["id"]),
    )
    get_db().commit()
    flash("Gallery visibility saved.", "success")
    return redirect(url_for("item_detail", item_id=image["item_id"]))


@app.route("/items/<int:item_id>/images/gallery", methods=("POST",))
@login_required
def update_item_image_gallery(item_id):
    db = get_db()
    item = get_item(item_id)
    collection = get_collection(item["collection_id"])
    if not user_can_manage_collection(collection):
        abort(403)
    images = db.execute("select id from item_images where item_id = ?", (item_id,)).fetchall()
    image_ids = {image["id"] for image in images}
    selected_ids = []
    for raw_id in request.form.getlist("gallery_image_ids"):
        try:
            image_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if image_id in image_ids:
            selected_ids.append(image_id)
    selected_ids = list(dict.fromkeys(selected_ids))
    def requested_gallery_position(image_id):
        try:
            return int(request.form.get(f"gallery_position_{image_id}") or 0)
        except (TypeError, ValueError):
            return 0
    ordered_ids = sorted(
        selected_ids,
        key=lambda image_id: (
            requested_gallery_position(image_id),
            selected_ids.index(image_id),
        ),
    )
    db.execute("update item_images set gallery_enabled = 0 where item_id = ?", (item_id,))
    for position, image_id in enumerate(ordered_ids):
        db.execute(
            "update item_images set gallery_enabled = 1, gallery_position = ? where id = ?",
            (position, image_id),
        )
    db.commit()
    if is_background_request():
        return jsonify({"ok": True})
    flash("Gallery images saved.", "success")
    return redirect(url_for("item_detail", item_id=item_id))


@app.route("/items/<int:item_id>/gallery", methods=("POST",))
@login_required
def update_item_gallery(item_id):
    db = get_db()
    item = db.execute("select * from items where id = ?", (item_id,)).fetchone()
    if item is None:
        abort(404)
    collection = get_collection(item["collection_id"])
    if not user_can_manage_collection(collection):
        abort(403)
    gallery_enabled = 1 if request.form.get("gallery_enabled") == "on" else 0
    db.execute("update item_images set gallery_enabled = ? where item_id = ?", (gallery_enabled, item_id))
    db.commit()
    flash("Gallery visibility saved.", "success")
    return redirect(url_for("collection_view", collection_id=collection["id"], view="table"))


@app.route("/collections/new", methods=("GET", "POST"))
@login_required
def new_collection():
    if not user_can_create_collection():
        abort(403)
    db = get_db()
    collectors = db.execute(
        "select id, username from users where role in ('admin', 'collector') order by username"
    ).fetchall()
    if request.method == "POST":
        owner_id = valid_collection_owner_id(g.user["id"], g.user["id"])
        if g.user["role"] == "admin":
            owner_id = valid_collection_owner_id(request.form.get("owner_id"), g.user["id"])
        cursor = db.execute(
            "insert into collections (name, description, owner_id) values (?, ?, ?)",
            (request.form["name"].strip(), request.form.get("description", "").strip(), owner_id),
        )
        for position, field_line in enumerate(request.form.get("fields", "").splitlines()):
            field_name, field_type = parse_field_definition(field_line)
            if field_name:
                db.execute(
                    "insert into fields (collection_id, name, field_type, position) values (?, ?, ?, ?)",
                    (cursor.lastrowid, field_name, field_type, position),
                )
        db.commit()
        return redirect(url_for("collection_view", collection_id=cursor.lastrowid))
    return render_template("collection_form.html", collection=None, collectors=collectors)


@app.route("/collections/<int:collection_id>/edit", methods=("GET", "POST"))
@collection_manager_required
def edit_collection(collection):
    db = get_db()
    collectors = db.execute(
        "select id, username from users where role in ('admin', 'collector') order by username"
    ).fetchall()
    if request.method == "POST":
        owner_id = collection["owner_id"]
        if g.user["role"] == "admin":
            owner_id = valid_collection_owner_id(request.form.get("owner_id"), collection["owner_id"] or g.user["id"])
        db.execute(
            "update collections set name = ?, description = ?, owner_id = ? where id = ?",
            (
                request.form["name"].strip(),
                request.form.get("description", "").strip(),
                owner_id,
                collection["id"],
            ),
        )
        next_position = db.execute(
            "select coalesce(max(position), -1) + 1 from fields where collection_id = ?",
            (collection["id"],),
        ).fetchone()[0]
        for field_line in request.form.get("fields", "").splitlines():
            field_name, field_type = parse_field_definition(field_line)
            if field_name:
                db.execute(
                    "insert into fields (collection_id, name, field_type, position) values (?, ?, ?, ?)",
                    (collection["id"], field_name, field_type, next_position),
                )
                next_position += 1
        db.commit()
        flash("Collection saved.", "success")
        return redirect(url_for("collection_view", collection_id=collection["id"]))
    return render_template("collection_form.html", collection=collection, collectors=collectors)


@app.route("/collections/<int:collection_id>")
@login_required
def collection_view(collection_id):
    collection = get_collection(collection_id)
    if not user_can_view_collection(collection):
        abort(403)
    fields = get_fields(collection_id)
    settings = {row["key"]: row["value"] for row in get_db().execute("select * from settings")}
    view = request.args.get("view", settings.get("default_view", "table"))
    query = request.args.get("q", "").strip().lower()
    tag = request.args.get("tag", "").strip().lower()
    image_status = request.args.get("image_status", "")
    gallery_status = request.args.get("gallery_status", "")
    sort = request.args.get("sort", "name")
    direction = request.args.get("dir", "asc")
    field_filters = {}
    field_ranges = {}
    for field in fields:
        raw_value = request.args.get(f"field_{field['id']}", "").strip()
        raw_min = request.args.get(f"field_{field['id']}_min", "").strip()
        raw_max = request.args.get(f"field_{field['id']}_max", "").strip()
        field_filters[field["id"]] = raw_value
        field_ranges[field["id"]] = {"min": raw_min, "max": raw_max}
    items = collection_items(collection_id)

    if query:
        items = [
            item
            for item in items
            if query in item["name"].lower()
            or query in item["description"].lower()
            or any(query in value.lower() for value in item["fields"].values())
            or any(query in item_tag for item_tag in item["tags"])
        ]
    if tag:
        items = [item for item in items if tag in item["tags"]]
    if image_status == "with":
        items = [item for item in items if item["has_images"]]
    elif image_status == "without":
        items = [item for item in items if not item["has_images"]]
    if gallery_status == "shown":
        items = [item for item in items if item["gallery_enabled"]]
    elif gallery_status == "hidden":
        items = [item for item in items if not item["gallery_enabled"]]
    for field in fields:
        value = field_filters.get(field["id"], "")
        field_name = field["name"]
        if value:
            if field["field_type"] == "checkbox":
                items = [item for item in items if item["fields"].get(field_name, "") == value]
            else:
                value_lower = value.lower()
                items = [item for item in items if value_lower in item["fields"].get(field_name, "").lower()]
        value_range = field_ranges.get(field["id"], {})
        if value_range.get("min") or value_range.get("max"):
            items = [
                item
                for item in items
                if value_matches_range(
                    item["fields"].get(field_name, ""),
                    value_range.get("min"),
                    value_range.get("max"),
                    field["field_type"],
                )
            ]

    reverse = direction == "desc"
    if sort in [field["name"] for field in fields]:
        items.sort(key=lambda item: item["fields"].get(sort, "").lower(), reverse=reverse)
    else:
        items.sort(key=lambda item: str(item.get(sort, "")).lower(), reverse=reverse)

    return render_template(
        "collection.html",
        collection=collection,
        fields=fields,
        items=items,
        view=view,
        query=query,
        tag=tag,
        image_status=image_status,
        gallery_status=gallery_status,
        field_filters=field_filters,
        field_ranges=field_ranges,
        sort=sort,
        direction=direction,
        can_manage=user_can_manage_collection(collection),
    )


@app.route("/collections/<int:collection_id>/items/new", methods=("GET", "POST"))
@collection_manager_required
def new_item(collection):
    collection_id = collection["id"]
    fields = get_fields(collection_id)
    if request.method == "POST":
        db = get_db()
        cursor = db.execute(
            "insert into items (collection_id, name, description) values (?, ?, ?)",
            (collection_id, request.form["name"].strip(), request.form.get("description", "").strip()),
        )
        item_id = cursor.lastrowid
        for field in fields:
            db.execute(
                "insert into item_values (item_id, field_id, value) values (?, ?, ?)",
                (item_id, field["id"], field_form_value(field, request.form)),
            )
        replace_item_tags(item_id, parse_tags(request.form.get("tags", "")))
        save_uploaded_images(item_id, request.files.getlist("images"))
        db.commit()
        return redirect(url_for("item_detail", item_id=item_id))
    return render_template(
        "item_form.html",
        collection=collection,
        fields=fields,
        field_suggestions=get_field_suggestions(collection_id),
        item=None,
        tags="",
    )


@app.route("/items/<int:item_id>")
def item_detail(item_id):
    db = get_db()
    item = db.execute("select * from items where id = ?", (item_id,)).fetchone()
    if item is None:
        abort(404)
    collection = get_collection(item["collection_id"])
    can_manage = user_can_manage_collection(collection)
    if not can_manage and not item_is_public(item_id):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        abort(403)
    fields = get_fields(item["collection_id"])
    values = {
        row["field_id"]: row["value"]
        for row in db.execute("select field_id, value from item_values where item_id = ?", (item_id,))
    }
    images = db.execute(
        """
        select * from item_images
        where item_id = ? and (? = 1 or gallery_enabled = 1)
        order by gallery_enabled desc, gallery_position, position, id
        """,
        (item_id, 1 if can_manage else 0),
    ).fetchall()
    return render_template(
        "item_detail.html",
        item=item,
        collection=collection,
        fields=fields,
        values=values,
        images=images,
        tags=get_item_tags(item_id),
        can_manage=can_manage,
    )


@app.route("/items/<int:item_id>/edit", methods=("GET", "POST"))
@login_required
def edit_item(item_id):
    db = get_db()
    item = db.execute("select * from items where id = ?", (item_id,)).fetchone()
    if item is None:
        abort(404)
    collection = get_collection(item["collection_id"])
    if not user_can_manage_collection(collection):
        abort(403)
    fields = get_fields(item["collection_id"])
    if request.method == "POST":
        db.execute(
            "update items set name = ?, description = ?, updated_at = current_timestamp where id = ?",
            (request.form["name"].strip(), request.form.get("description", "").strip(), item_id),
        )
        for field in fields:
            db.execute(
                """
                insert into item_values (item_id, field_id, value) values (?, ?, ?)
                on conflict(item_id, field_id) do update set value = excluded.value
                """,
                (item_id, field["id"], field_form_value(field, request.form)),
            )
        replace_item_tags(item_id, parse_tags(request.form.get("tags", "")))
        save_uploaded_images(item_id, request.files.getlist("images"))
        db.commit()
        return redirect(url_for("item_detail", item_id=item_id))
    values = {
        row["field_id"]: row["value"]
        for row in db.execute("select field_id, value from item_values where item_id = ?", (item_id,))
    }
    return render_template(
        "item_form.html",
        collection=collection,
        fields=fields,
        field_suggestions=get_field_suggestions(item["collection_id"]),
        item=item,
        values=values,
        tags=", ".join(get_item_tags(item_id)),
    )


@app.route("/items/<int:item_id>/duplicate", methods=("POST",))
@login_required
def duplicate_item(item_id):
    db = get_db()
    item = get_item(item_id)
    collection = get_collection(item["collection_id"])
    if not user_can_manage_collection(collection):
        abort(403)
    cursor = db.execute(
        "insert into items (collection_id, name, description) values (?, ?, ?)",
        (item["collection_id"], f"{item['name']} copy", item["description"]),
    )
    new_item_id = cursor.lastrowid
    values = db.execute("select field_id, value from item_values where item_id = ?", (item_id,)).fetchall()
    for value in values:
        db.execute(
            "insert into item_values (item_id, field_id, value) values (?, ?, ?)",
            (new_item_id, value["field_id"], value["value"]),
        )
    tags = get_item_tags(item_id)
    replace_item_tags(new_item_id, tags)
    db.commit()
    flash("Item duplicated.", "success")
    return redirect(url_for("edit_item", item_id=new_item_id))


@app.route("/items/<int:item_id>/delete", methods=("POST",))
@login_required
def delete_item(item_id):
    db = get_db()
    item = get_item(item_id)
    collection = get_collection(item["collection_id"])
    if not user_can_manage_collection(collection):
        abort(403)
    images = db.execute("select filename from item_images where item_id = ?", (item_id,)).fetchall()
    db.execute("delete from items where id = ?", (item_id,))
    db.commit()
    for image in images:
        try:
            (UPLOAD_DIR / image["filename"]).unlink()
        except FileNotFoundError:
            pass
    flash("Item deleted.", "success")
    return redirect(url_for("collection_view", collection_id=collection["id"]))


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    image = get_db().execute(
        """
        select item_images.*, collections.owner_id
        from item_images
        join items on items.id = item_images.item_id
        join collections on collections.id = items.collection_id
        where item_images.filename = ?
        """,
        (filename,),
    ).fetchone()
    if image is None or not user_can_view_image(image):
        abort(404)
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/assets/<path:filename>")
def asset_file(filename):
    return send_from_directory(ASSET_DIR, filename)


@app.route("/admin")
@admin_required
def admin_index():
    return redirect(url_for("admin_users"))


@app.route("/admin/applicants")
@admin_required
def admin_applicants():
    applications = get_db().execute(
        "select id, username, created_at from user_applications order by created_at"
    ).fetchall()
    return render_template("admin_applicants.html", applications=applications)


@app.route("/admin/applicants/<int:application_id>/approve", methods=("POST",))
@admin_required
def approve_applicant(application_id):
    db = get_db()
    application = db.execute("select * from user_applications where id = ?", (application_id,)).fetchone()
    if application is None:
        abort(404)
    if username_exists(application["username"]):
        flash("That username is already in use. The application was not approved.", "error")
        return redirect(url_for("admin_applicants"))
    db.execute(
        "insert into users (username, password_hash, role) values (?, ?, 'collector')",
        (application["username"], application["password_hash"]),
    )
    db.execute("delete from user_applications where id = ?", (application_id,))
    db.commit()
    flash(f"{application['username']} was approved.", "success")
    return redirect(url_for("admin_applicants"))


@app.route("/admin/applicants/<int:application_id>/decline", methods=("POST",))
@admin_required
def decline_applicant(application_id):
    db = get_db()
    application = db.execute("select username from user_applications where id = ?", (application_id,)).fetchone()
    if application is None:
        abort(404)
    db.execute("delete from user_applications where id = ?", (application_id,))
    db.commit()
    flash(f"{application['username']} was declined.", "success")
    return redirect(url_for("admin_applicants"))


@app.route("/admin/users", methods=("GET", "POST"))
@admin_required
def admin_users():
    db = get_db()
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        role = normalize_role(request.form.get("role", "collector"))
        if username_exists(username) or application_exists(username):
            flash("That username is already taken or awaiting approval.", "error")
        elif username and password:
            db.execute(
                "insert into users (username, password_hash, role) values (?, ?, ?)",
                (username, generate_password_hash(password), role),
            )
            db.commit()
            flash("User created.", "success")
    users = db.execute("select id, username, role, created_at from users order by username").fetchall()
    return render_template("admin_users.html", users=users)


@app.route("/admin/collections")
@admin_required
def admin_collections():
    collections = get_db().execute(
        """
        select collections.*, users.username as owner_username
        from collections
        left join users on users.id = collections.owner_id
        order by collections.name
        """
    ).fetchall()
    return render_template("admin_collections.html", collections=collections)


@app.route("/admin/fields", methods=("GET", "POST"))
@admin_required
def admin_fields():
    db = get_db()
    collections = db.execute("select * from collections order by name").fetchall()
    selected_id = int(request.form.get("collection_id") or request.args.get("collection_id") or collections[0]["id"]) if collections else None
    if request.method == "POST" and selected_id:
        name = request.form["name"].strip()
        field_type = normalize_field_type(request.form.get("field_type", "text"))
        if name:
            position = db.execute(
                "select coalesce(max(position), -1) + 1 from fields where collection_id = ?", (selected_id,)
            ).fetchone()[0]
            db.execute(
                "insert into fields (collection_id, name, field_type, position) values (?, ?, ?, ?)",
                (selected_id, name, field_type, position),
            )
            db.commit()
    fields = get_fields(selected_id) if selected_id else []
    return render_template(
        "admin_fields.html",
        collections=collections,
        selected_id=selected_id,
        fields=fields,
        field_type_choices=FIELD_TYPE_CHOICES,
        field_type_labels=dict(FIELD_TYPE_CHOICES),
    )


@app.route("/admin/fields/<int:field_id>/update", methods=("POST",))
@admin_required
def update_field(field_id):
    db = get_db()
    field = get_field(field_id)
    name = request.form["name"].strip()
    field_type = normalize_field_type(request.form.get("field_type", field["field_type"]))
    try:
        position = max(0, int(request.form.get("position", field["position"])))
    except (TypeError, ValueError):
        position = field["position"]
    if name:
        db.execute(
            "update fields set name = ?, field_type = ?, position = ? where id = ?",
            (name, field_type, position, field["id"]),
        )
        db.commit()
        flash("Field saved.", "success")
    return redirect(url_for("admin_fields", collection_id=field["collection_id"]))


@app.route("/admin/fields/<int:field_id>/delete", methods=("POST",))
@admin_required
def delete_field(field_id):
    db = get_db()
    field = get_field(field_id)
    db.execute("delete from item_values where field_id = ?", (field["id"],))
    db.execute("delete from fields where id = ?", (field["id"],))
    db.commit()
    flash("Field deleted.", "success")
    return redirect(url_for("admin_fields", collection_id=field["collection_id"]))


@app.route("/admin/settings", methods=("GET", "POST"))
@admin_required
def admin_settings():
    db = get_db()
    if request.method == "POST":
        for key in ("site_name", "default_view"):
            db.execute(
                "insert into settings (key, value) values (?, ?) on conflict(key) do update set value = excluded.value",
                (key, request.form.get(key, "").strip()),
            )
        db.commit()
        flash("Settings saved.", "success")
    settings = {row["key"]: row["value"] for row in db.execute("select * from settings")}
    return render_template("admin_settings.html", settings=settings)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=os.environ.get("FLASK_DEBUG") == "1")
