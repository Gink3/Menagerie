import os
import sqlite3
import uuid
from functools import wraps
from pathlib import Path

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
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("MENAGERIE_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DATABASE = DATA_DIR / "menagerie.sqlite"
ALLOWED_IMAGE_EXTENSIONS = {"avif", "gif", "jpeg", "jpg", "png", "webp"}
ROLES = {"admin", "collector", "viewer"}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("MENAGERIE_SECRET_KEY", "dev-change-me")
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024


def get_db():
    if "db" not in g:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
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
    image_columns = [row["name"] for row in db.execute("pragma table_info(item_images)").fetchall()]
    image_defaults = {
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
    db.commit()


@app.before_request
def load_current_user():
    init_db()
    user_id = session.get("user_id")
    g.user = None
    if user_id:
        g.user = get_db().execute("select * from users where id = ?", (user_id,)).fetchone()


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


def user_can_create_collection():
    return g.user is not None and g.user["role"] in {"admin", "collector"}


def user_can_manage_collection(collection):
    if g.user is None:
        return False
    if g.user["role"] == "admin":
        return True
    return g.user["role"] == "collector" and collection["owner_id"] == g.user["id"]


def user_can_manage_image(image):
    if g.user is None:
        return False
    if g.user["role"] == "admin":
        return True
    return g.user["role"] == "collector" and image["owner_id"] == g.user["id"]


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
    order = "users.username, item_images.gallery_y, item_images.gallery_x, item_images.position, item_images.id"
    if mixed:
        order = "item_images.created_at desc, item_images.id desc"
    return get_db().execute(
        f"""
        select item_images.*, items.id as item_id, items.name as item_name,
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


def get_fields(collection_id):
    return get_db().execute(
        "select * from fields where collection_id = ? order by position, id", (collection_id,)
    ).fetchall()


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
        gallery_x = (current_position * 2) % 6
        gallery_y = (current_position * 2) // 6
        db.execute(
            """
            insert into item_images (item_id, filename, original_name, position, gallery_x, gallery_y)
            values (?, ?, ?, ?, ?, ?)
            """,
            (item_id, filename, original_name, current_position, gallery_x, gallery_y),
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
        select item_id, filename from item_images
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
        item["primary_image"] = None
    for row in field_rows:
        next(item for item in items if item["id"] == row["item_id"])["fields"][row["name"]] = row["value"]
    for row in tag_rows:
        next(item for item in items if item["id"] == row["item_id"])["tags"].append(row["name"])
    for row in images:
        item = next(item for item in items if item["id"] == row["item_id"])
        if item["primary_image"] is None:
            item["primary_image"] = row["filename"]
    return items


@app.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = get_db().execute("select * from users where username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            return redirect(request.args.get("next") or url_for("collections_index"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


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


@app.route("/users/<int:user_id>/gallery")
def user_gallery(user_id):
    owner = get_user(user_id)
    editable = g.user is not None and (g.user["role"] == "admin" or g.user["id"] == owner["id"])
    return render_template("gallery.html", images=gallery_images(owner["id"]), owner=owner, editable=editable)


@app.route("/gallery/images/<int:image_id>")
def gallery_image_detail(image_id):
    image = get_image(image_id)
    return render_template("gallery_image.html", image=image)


@app.route("/gallery/layout", methods=("POST",))
@login_required
def save_gallery_layout():
    db = get_db()
    updates = request.get_json(silent=True) or []
    for update in updates:
        image = get_image(update.get("id"))
        if not user_can_manage_image(image):
            abort(403)
        x = max(0, min(5, int(update.get("x", image["gallery_x"]))))
        y = max(0, int(update.get("y", image["gallery_y"])))
        w = max(1, min(6, int(update.get("w", image["gallery_w"]))))
        h = max(1, min(6, int(update.get("h", image["gallery_h"]))))
        if x + w > 6:
            x = 6 - w
        db.execute(
            "update item_images set gallery_x = ?, gallery_y = ?, gallery_w = ?, gallery_h = ? where id = ?",
            (x, y, w, h, image["id"]),
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
        flash("Image settings saved.", "success")
        return redirect(url_for("item_detail", item_id=image["item_id"]))
    return render_template("image_crop.html", image=image)


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
        for position, field_name in enumerate(request.form.get("fields", "").splitlines()):
            field_name = field_name.strip()
            if field_name:
                db.execute(
                    "insert into fields (collection_id, name, position) values (?, ?, ?)",
                    (cursor.lastrowid, field_name, position),
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
        for field_name in request.form.get("fields", "").splitlines():
            field_name = field_name.strip()
            if field_name:
                db.execute(
                    "insert into fields (collection_id, name, position) values (?, ?, ?)",
                    (collection["id"], field_name, next_position),
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
    fields = get_fields(collection_id)
    settings = {row["key"]: row["value"] for row in get_db().execute("select * from settings")}
    view = request.args.get("view", settings.get("default_view", "table"))
    query = request.args.get("q", "").strip().lower()
    tag = request.args.get("tag", "").strip().lower()
    sort = request.args.get("sort", "name")
    direction = request.args.get("dir", "asc")
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
                (item_id, field["id"], request.form.get(f"field_{field['id']}", "").strip()),
            )
        replace_item_tags(item_id, parse_tags(request.form.get("tags", "")))
        save_uploaded_images(item_id, request.files.getlist("images"))
        db.commit()
        return redirect(url_for("item_detail", item_id=item_id))
    return render_template("item_form.html", collection=collection, fields=fields, item=None, tags="")


@app.route("/items/<int:item_id>")
@login_required
def item_detail(item_id):
    db = get_db()
    item = db.execute("select * from items where id = ?", (item_id,)).fetchone()
    if item is None:
        abort(404)
    collection = get_collection(item["collection_id"])
    fields = get_fields(item["collection_id"])
    values = {
        row["field_id"]: row["value"]
        for row in db.execute("select field_id, value from item_values where item_id = ?", (item_id,))
    }
    images = db.execute("select * from item_images where item_id = ? order by position, id", (item_id,)).fetchall()
    return render_template(
        "item_detail.html",
        item=item,
        collection=collection,
        fields=fields,
        values=values,
        images=images,
        tags=get_item_tags(item_id),
        can_manage=user_can_manage_collection(collection),
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
                (item_id, field["id"], request.form.get(f"field_{field['id']}", "").strip()),
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
        item=item,
        values=values,
        tags=", ".join(get_item_tags(item_id)),
    )


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/admin")
@admin_required
def admin_index():
    return redirect(url_for("admin_users"))


@app.route("/admin/users", methods=("GET", "POST"))
@admin_required
def admin_users():
    db = get_db()
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        role = normalize_role(request.form.get("role", "collector"))
        if username and password:
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
        field_type = request.form.get("field_type", "text")
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
    return render_template("admin_fields.html", collections=collections, selected_id=selected_id, fields=fields)


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
