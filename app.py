import os
import sys
import api_keys
import scan_api
import capture_fields
import dropdown_fields
import json
import re
import random
import secrets
import sqlite3
import time
import uuid
import xml.etree.ElementTree as ET
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse, urlencode

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
import bleach
from markdown import markdown
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
ASSET_DIR = BASE_DIR / "assets"
DATA_DIR = Path(os.environ.get("MENAGERIE_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
WIKI_UPLOAD_DIR = UPLOAD_DIR / "wiki"
DATABASE = DATA_DIR / "menagerie.sqlite"
ALLOWED_IMAGE_EXTENSIONS = {"avif", "gif", "jpeg", "jpg", "png", "webp"}
ROLES = {"admin", "collector", "viewer"}
CAPABILITIES = {
    "label_images": "Label images",
    "review_annotations": "Review annotations",
    "upload_svg": "Upload SVG assets",
    "manage_symbols": "Manage symbols",
    "manage_heraldry": "Manage heraldry",
    "edit_wiki": "Edit wiki pages",
    "admin_ai": "Administer AI workflows",
    "deploy_models": "Deploy AI models",
}
FIELD_TYPE_CHOICES = (
    ("text", "Single line text"),
    ("multiline", "Multi-line text"),
    ("number", "Number"),
    ("date", "Date"),
    ("timestamp", "Timestamp"),
    ("url", "URL"),
    ("checkbox", "Checkbox"),
    ("dropdown", "Dropdown"),
)
FIELD_TYPES = {value for value, label in FIELD_TYPE_CHOICES}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("MENAGERIE_SECRET_KEY", "dev-change-me")
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MENAGERIE_MAX_UPLOAD_MB", "128")) * 1024 * 1024
app.config["MINI_SCAN_REQUIRED_FIELDS"] = json.loads(os.environ.get("MENAGERIE_MINI_SCAN_REQUIRED_FIELDS", "{}"))
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = os.environ.get("MENAGERIE_SESSION_COOKIE_SAMESITE", "Lax")
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("MENAGERIE_SESSION_COOKIE_SECURE", "0") == "1"
app.config["SESSION_COOKIE_NAME"] = os.environ.get("MENAGERIE_SESSION_COOKIE_NAME", "menagerie_session_v2")
if os.environ.get("MENAGERIE_PROXY_FIX", "1") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

RATE_LIMITS = {}


def get_db():
    if "db" not in g:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        WIKI_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("pragma foreign_keys = on")
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def migration_001_foundation(db):
    db.executescript(
        """
        create table if not exists user_capabilities (
            user_id integer not null references users(id) on delete cascade,
            capability text not null,
            enabled integer not null default 1,
            created_at text not null default current_timestamp,
            primary key (user_id, capability)
        );

        create table if not exists knowledge_objects (
            id integer primary key autoincrement,
            object_type text not null,
            display_name text not null,
            created_by integer references users(id),
            created_at text not null default current_timestamp,
            updated_at text not null default current_timestamp
        );

        create table if not exists knowledge_relationships (
            id integer primary key autoincrement,
            source_object_id integer not null references knowledge_objects(id) on delete cascade,
            relationship_type text not null,
            target_object_id integer not null references knowledge_objects(id) on delete cascade,
            metadata text not null default '{}',
            created_by integer references users(id),
            created_at text not null default current_timestamp,
            unique (source_object_id, relationship_type, target_object_id)
        );

        create table if not exists knowledge_revisions (
            id integer primary key autoincrement,
            object_id integer not null references knowledge_objects(id) on delete cascade,
            revision_number integer not null,
            snapshot text not null,
            created_by integer references users(id),
            created_at text not null default current_timestamp,
            unique (object_id, revision_number)
        );
        """
    )


def migration_002_symbol_atlas(db):
    db.executescript(
        """
        create table if not exists symbol_categories (
            id integer primary key autoincrement,
            name text not null unique,
            description text not null default '',
            created_at text not null default current_timestamp
        );

        create table if not exists symbols (
            id integer primary key autoincrement,
            name text not null,
            description text not null default '',
            meaning text not null default '',
            category_id integer references symbol_categories(id) on delete set null,
            created_by integer references users(id),
            created_at text not null default current_timestamp,
            updated_at text not null default current_timestamp
        );

        create table if not exists symbol_versions (
            id integer primary key autoincrement,
            symbol_id integer not null references symbols(id) on delete cascade,
            version_number integer not null,
            svg_filename text not null,
            change_summary text not null default '',
            created_by integer references users(id),
            created_at text not null default current_timestamp,
            unique (symbol_id, version_number)
        );

        create table if not exists symbol_variants (
            id integer primary key autoincrement,
            symbol_id integer not null references symbols(id) on delete cascade,
            name text not null,
            description text not null default '',
            svg_filename text,
            created_by integer references users(id),
            created_at text not null default current_timestamp
        );

        create table if not exists symbol_wiki_links (
            symbol_id integer not null references symbols(id) on delete cascade,
            knowledge_object_id integer not null references knowledge_objects(id) on delete cascade,
            primary key (symbol_id, knowledge_object_id)
        );
        """
    )


def migration_003_heraldry_studio(db):
    db.executescript(
        """
        create table if not exists heraldry_templates (
            id integer primary key autoincrement,
            name text not null unique,
            definition text not null,
            created_at text not null default current_timestamp
        );

        create table if not exists heraldry_designs (
            id integer primary key autoincrement,
            knowledge_object_id integer unique references knowledge_objects(id) on delete set null,
            name text not null,
            template_id integer not null references heraldry_templates(id),
            definition text not null default '{}',
            created_by integer references users(id),
            created_at text not null default current_timestamp,
            updated_at text not null default current_timestamp
        );

        create table if not exists heraldry_elements (
            id integer primary key autoincrement,
            design_id integer not null references heraldry_designs(id) on delete cascade,
            symbol_id integer not null references symbols(id),
            element_type text not null default 'symbol',
            x real not null default 50,
            y real not null default 50,
            width real not null default 20,
            height real not null default 20,
            rotation real not null default 0,
            mirrored integer not null default 0,
            layer_order integer not null default 0
        );

        create table if not exists heraldry_revisions (
            id integer primary key autoincrement,
            design_id integer not null references heraldry_designs(id) on delete cascade,
            revision_number integer not null,
            snapshot text not null,
            created_by integer references users(id),
            created_at text not null default current_timestamp,
            unique (design_id, revision_number)
        );
        """
    )
    defaults = {
        "Classic shield": {"viewBox": "0 0 400 500", "path": "M 20 20 H 380 V 260 C 380 390 200 480 200 480 C 200 480 20 390 20 260 Z"},
        "Round shield": {"viewBox": "0 0 400 500", "path": "M 200 20 C 310 20 380 90 380 220 C 380 370 280 460 200 480 C 120 460 20 370 20 220 C 20 90 90 20 200 20 Z"},
        "Banner shield": {"viewBox": "0 0 400 500", "path": "M 20 20 H 380 V 300 L 200 480 L 20 300 Z"},
    }
    for name, definition in defaults.items():
        db.execute("insert or ignore into heraldry_templates (name, definition) values (?, ?)", (name, json.dumps(definition)))


def migration_004_lineage(db):
    db.executescript(
        """
        create table if not exists houses (
            id integer primary key autoincrement,
            name text not null unique,
            description text not null default '',
            created_by integer references users(id),
            created_at text not null default current_timestamp
        );
        create table if not exists people (
            id integer primary key autoincrement,
            name text not null,
            biography text not null default '',
            birth_date text not null default '',
            death_date text not null default '',
            created_by integer references users(id),
            created_at text not null default current_timestamp,
            updated_at text not null default current_timestamp
        );
        create table if not exists titles (
            id integer primary key autoincrement,
            name text not null unique,
            description text not null default ''
        );
        create table if not exists person_parents (
            parent_id integer not null references people(id) on delete cascade,
            child_id integer not null references people(id) on delete cascade,
            primary key (parent_id, child_id),
            check (parent_id <> child_id)
        );
        create table if not exists person_partners (
            person_id integer not null references people(id) on delete cascade,
            partner_id integer not null references people(id) on delete cascade,
            relationship_type text not null default 'marriage',
            primary key (person_id, partner_id),
            check (person_id <> partner_id)
        );
        create table if not exists person_house_memberships (
            person_id integer not null references people(id) on delete cascade,
            house_id integer not null references houses(id) on delete cascade,
            role text not null default '',
            primary key (person_id, house_id)
        );
        create table if not exists person_titles (
            person_id integer not null references people(id) on delete cascade,
            title_id integer not null references titles(id) on delete cascade,
            start_date text not null default '',
            end_date text not null default '',
            primary key (person_id, title_id)
        );
        create table if not exists person_heraldry (
            person_id integer not null references people(id) on delete cascade,
            design_id integer not null references heraldry_designs(id) on delete cascade,
            relationship_type text not null default 'associated',
            primary key (person_id, design_id)
        );
        create table if not exists heraldry_succession (
            predecessor_id integer not null references heraldry_designs(id) on delete cascade,
            successor_id integer not null references heraldry_designs(id) on delete cascade,
            description text not null default '',
            primary key (predecessor_id, successor_id),
            check (predecessor_id <> successor_id)
        );
        create table if not exists person_item_links (
            person_id integer not null references people(id) on delete cascade,
            item_id integer not null references items(id) on delete cascade,
            relationship_type text not null default 'associated',
            primary key (person_id, item_id)
        );
        """
    )


def migration_005_wiki(db):
    db.executescript(
        """
        create table if not exists wiki_pages (
            id integer primary key autoincrement,
            knowledge_object_id integer not null unique references knowledge_objects(id) on delete cascade,
            slug text not null unique,
            page_type text not null,
            title text not null,
            body text not null default '',
            created_by integer references users(id),
            created_at text not null default current_timestamp,
            updated_at text not null default current_timestamp
        );
        create table if not exists wiki_revisions (
            id integer primary key autoincrement,
            page_id integer not null references wiki_pages(id) on delete cascade,
            revision_number integer not null,
            title text not null,
            body text not null,
            created_by integer references users(id),
            created_at text not null default current_timestamp,
            unique (page_id, revision_number)
        );
        create table if not exists wiki_page_categories (
            page_id integer not null references wiki_pages(id) on delete cascade,
            category text not null,
            primary key (page_id, category)
        );
        create table if not exists wiki_page_links (
            source_page_id integer not null references wiki_pages(id) on delete cascade,
            target_page_id integer not null references wiki_pages(id) on delete cascade,
            relationship_type text not null default 'related',
            primary key (source_page_id, target_page_id, relationship_type),
            check (source_page_id <> target_page_id)
        );
        """
    )


def migration_006_annotations(db):
    db.executescript(
        """
        create table if not exists annotations (
            id integer primary key autoincrement,
            image_id integer not null references item_images(id) on delete cascade,
            symbol_id integer not null references symbols(id),
            geometry_type text not null,
            geometry text not null,
            status text not null default 'draft',
            labeler_id integer references users(id),
            reviewer_id integer references users(id),
            created_at text not null default current_timestamp,
            updated_at text not null default current_timestamp
        );
        create table if not exists annotation_revisions (
            id integer primary key autoincrement,
            annotation_id integer not null references annotations(id) on delete cascade,
            revision_number integer not null,
            geometry_type text not null,
            geometry text not null,
            symbol_id integer not null references symbols(id),
            status text not null,
            created_by integer references users(id),
            created_at text not null default current_timestamp,
            unique (annotation_id, revision_number)
        );
        create table if not exists annotation_reviews (
            id integer primary key autoincrement,
            annotation_id integer not null references annotations(id) on delete cascade,
            reviewer_id integer not null references users(id),
            decision text not null,
            note text not null default '',
            created_at text not null default current_timestamp
        );
        """
    )


def migration_007_ai_lab(db):
    db.executescript(
        """
        create table if not exists ai_classes (
            id integer primary key autoincrement,
            name text not null unique,
            symbol_id integer references symbols(id) on delete set null,
            created_at text not null default current_timestamp
        );
        create table if not exists ai_datasets (
            id integer primary key autoincrement,
            name text not null unique,
            description text not null default '',
            created_by integer references users(id),
            created_at text not null default current_timestamp
        );
        create table if not exists ai_dataset_versions (
            id integer primary key autoincrement,
            dataset_id integer not null references ai_datasets(id) on delete cascade,
            version_number integer not null,
            sample_count integer not null default 0,
            split_seed integer not null,
            status text not null default 'ready',
            created_by integer references users(id),
            created_at text not null default current_timestamp,
            unique (dataset_id, version_number)
        );
        create table if not exists ai_dataset_samples (
            dataset_version_id integer not null references ai_dataset_versions(id) on delete cascade,
            annotation_id integer not null references annotations(id),
            class_id integer not null references ai_classes(id),
            split text not null,
            primary key (dataset_version_id, annotation_id)
        );
        create table if not exists ai_training_runs (
            id integer primary key autoincrement,
            dataset_version_id integer not null references ai_dataset_versions(id),
            status text not null default 'queued',
            parameters text not null default '{}',
            metrics text not null default '{}',
            error_message text not null default '',
            started_at text,
            completed_at text,
            created_by integer references users(id),
            created_at text not null default current_timestamp
        );
        create table if not exists ai_models (
            id integer primary key autoincrement,
            name text not null unique,
            description text not null default '',
            created_at text not null default current_timestamp
        );
        create table if not exists ai_model_versions (
            id integer primary key autoincrement,
            model_id integer not null references ai_models(id) on delete cascade,
            version_number integer not null,
            training_run_id integer references ai_training_runs(id),
            artifact_filename text,
            metrics text not null default '{}',
            created_by integer references users(id),
            created_at text not null default current_timestamp,
            unique (model_id, version_number)
        );
        create table if not exists ai_deployments (
            id integer primary key autoincrement,
            model_version_id integer not null references ai_model_versions(id),
            status text not null default 'active',
            deployed_by integer references users(id),
            deployed_at text not null default current_timestamp
        );
        create table if not exists ai_suggestions (
            id integer primary key autoincrement,
            image_id integer not null references item_images(id) on delete cascade,
            model_version_id integer references ai_model_versions(id),
            symbol_id integer references symbols(id),
            geometry_type text not null,
            geometry text not null,
            confidence real not null default 0,
            status text not null default 'pending',
            reviewed_by integer references users(id),
            created_at text not null default current_timestamp
        );
        """
    )


def migration_008_metrics(db):
    db.executescript(
        """
        create table if not exists activity_events (
            id integer primary key autoincrement,
            user_id integer references users(id) on delete set null,
            event_type text not null,
            object_type text not null default '',
            object_id integer,
            metadata text not null default '{}',
            created_at text not null default current_timestamp
        );
        create table if not exists audit_events (
            id integer primary key autoincrement,
            user_id integer references users(id) on delete set null,
            action text not null,
            object_type text not null default '',
            object_id integer,
            details text not null default '{}',
            created_at text not null default current_timestamp
        );
        create index if not exists activity_events_type_created on activity_events(event_type, created_at);
        create index if not exists audit_events_action_created on audit_events(action, created_at);
        """
    )


def migration_009_designer_and_lineage_ownership(db):
    """Keep personal miniature lineages private while making design assets reusable."""
    for table in ("houses", "people"):
        columns = {row["name"] for row in db.execute(f"pragma table_info({table})")}
        if "owner_id" not in columns:
            db.execute(f"alter table {table} add column owner_id integer references users(id)")
            first_user = db.execute("select id from users order by id limit 1").fetchone()
            if first_user:
                db.execute(f"update {table} set owner_id = ? where owner_id is null", (first_user["id"],))
    db.executescript("""
        create index if not exists houses_owner_name on houses(owner_id, name);
        create index if not exists people_owner_name on people(owner_id, name);
    """)


def migration_010_default_decal(db):
    """Seed a shared outline decal so every installation has an editor example."""
    if db.execute("select 1 from heraldry_designs where name = 'Donut decal'").fetchone():
        return
    template = db.execute("select id from heraldry_templates order by id limit 1").fetchone()
    if template is None:
        return
    markup = (ASSET_DIR / "default_decals" / "donut.svg").read_text(encoding="utf-8")
    definition = json.dumps({"fields": {"division": "plain", "primary": "#ffffff", "secondary": "#000000"}, "elements": [], "svg_markup": sanitize_svg(markup)})
    cursor = db.execute("insert into knowledge_objects (object_type, display_name) values ('heraldry', 'Donut decal')")
    db.execute("insert into heraldry_designs (knowledge_object_id, name, template_id, definition) values (?, 'Donut decal', ?, ?)", (cursor.lastrowid, template["id"], definition))


def migration_011_refresh_default_decal(db):
    """Refresh the seeded example for installations that received its first draft."""
    markup = (ASSET_DIR / "default_decals" / "donut.svg").read_text(encoding="utf-8")
    definition = json.dumps({"fields": {"division": "plain", "primary": "#ffffff", "secondary": "#000000"}, "elements": [], "svg_markup": sanitize_svg(markup)})
    db.execute("update heraldry_designs set definition = ?, updated_at = current_timestamp where name = 'Donut decal' and created_by is null", (definition,))


def migration_012_decal_templates(db):
    db.executescript("""
        create table if not exists decal_templates (
            id integer primary key autoincrement,
            name text not null unique,
            svg_markup text not null,
            created_by integer references users(id),
            created_at text not null default current_timestamp
        );
    """)
    markup = sanitize_svg((ASSET_DIR / "default_decals" / "donut.svg").read_text(encoding="utf-8"))
    db.execute("insert or ignore into decal_templates (name, svg_markup) values ('Donut decal', ?)", (markup,))


def migration_013_wiki_images(db):
    db.execute(
        """create table if not exists wiki_images (
            id integer primary key autoincrement,
            page_id integer not null references wiki_pages(id) on delete cascade,
            filename text not null unique,
            original_name text not null,
            uploaded_by integer references users(id),
            created_at text not null default current_timestamp
        )"""
    )


MIGRATIONS = (
    (1, "foundation", migration_001_foundation),
    (2, "symbol_atlas", migration_002_symbol_atlas),
    (3, "heraldry_studio", migration_003_heraldry_studio),
    (4, "lineage", migration_004_lineage),
    (5, "wiki", migration_005_wiki),
    (6, "annotations", migration_006_annotations),
    (7, "ai_lab", migration_007_ai_lab),
    (8, "metrics", migration_008_metrics),
    (9, "designer_and_lineage_ownership", migration_009_designer_and_lineage_ownership),
    (10, "default_decal", migration_010_default_decal),
    (11, "refresh_default_decal", migration_011_refresh_default_decal),
    (12, "decal_templates", migration_012_decal_templates),
    (13, "wiki_images", migration_013_wiki_images),
    (14, "api_keys", api_keys.migrate),
    (15, "mini_scan_identity", scan_api.migrate),
    (16, "dropdown_fields", dropdown_fields.migrate),
    (17, "capture_fields", capture_fields.migrate),
)


def run_migrations(db):
    db.execute(
        "create table if not exists schema_migrations (version integer primary key, name text not null, applied_at text not null default current_timestamp)"
    )
    applied = {row["version"] for row in db.execute("select version from schema_migrations")}
    for version, name, migration in MIGRATIONS:
        if version in applied:
            continue
        migration(db)
        db.execute("insert into schema_migrations (version, name) values (?, ?)", (version, name))


def create_knowledge_object(object_type, display_name, created_by=None):
    db = get_db()
    cursor = db.execute(
        "insert into knowledge_objects (object_type, display_name, created_by) values (?, ?, ?)",
        (object_type, display_name, created_by),
    )
    db.commit()
    return cursor.lastrowid


def add_knowledge_relationship(source_object_id, relationship_type, target_object_id, metadata="{}", created_by=None):
    db = get_db()
    cursor = db.execute(
        """
        insert into knowledge_relationships
            (source_object_id, relationship_type, target_object_id, metadata, created_by)
        values (?, ?, ?, ?, ?)
        on conflict(source_object_id, relationship_type, target_object_id)
        do update set metadata = excluded.metadata
        """,
        (source_object_id, relationship_type, target_object_id, metadata, created_by),
    )
    db.commit()
    return cursor.lastrowid


def has_capability(user, capability):
    if user is None or capability not in CAPABILITIES:
        return False
    if user["role"] == "admin":
        return True
    return (
        get_db()
        .execute(
            "select 1 from user_capabilities where user_id = ? and capability = ? and enabled = 1",
            (user["id"], capability),
        )
        .fetchone()
        is not None
    )


def capability_required(capability):
    def decorator(view):
        @wraps(view)
        def wrapped_view(**kwargs):
            if g.user is None:
                return redirect(url_for("login", next=request.path))
            if not has_capability(g.user, capability):
                abort(403)
            return view(**kwargs)

        return wrapped_view

    return decorator


def sanitize_svg(raw_svg):
    if not raw_svg:
        raise ValueError("The uploaded file is empty.")
    if "<!doctype" in raw_svg.lower() or "<!entity" in raw_svg.lower():
        raise ValueError("SVG contains a prohibited DOCTYPE or entity declaration.")
    try:
        root = ET.fromstring(raw_svg)
    except ET.ParseError as error:
        raise ValueError(f"SVG XML could not be parsed ({error}).") from error
    root_tag = root.tag.rsplit("}", 1)[-1] if isinstance(root.tag, str) else ""
    if root_tag != "svg":
        raise ValueError("The uploaded XML document is not an SVG (its root element must be <svg>).")
    allowed_tags = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "title", "desc", "defs", "linearGradient", "radialGradient", "stop", "use", "image"}
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1] if isinstance(element.tag, str) else ""
        if tag not in allowed_tags:
            raise ValueError("SVG contains an unsupported element.")
        for attribute, value in element.attrib.items():
            name = attribute.rsplit("}", 1)[-1].lower()
            is_embedded_png = tag == "image" and name in {"href", "xlink:href"} and value.startswith("data:image/png;base64,")
            if name.startswith("on") or name in {"href", "xlink:href"} and not value.startswith("#") and not is_embedded_png:
                raise ValueError("SVG contains an unsafe attribute.")
            if "javascript:" in value.lower() or "data:" in value.lower() and not is_embedded_png:
                raise ValueError("SVG contains an unsafe URL.")
    # Preserve the normal SVG namespace. Without this, ElementTree emits an
    # ns0: prefix which browsers can re-serialize into an invalid editor SVG.
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    return ET.tostring(root, encoding="unicode")


def valid_json_object(raw, message):
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as error:
        raise ValueError(message) from error
    if not isinstance(value, dict):
        raise ValueError(message)
    return json.dumps(value)


def save_symbol_svg(raw_svg):
    sanitized = sanitize_svg(raw_svg)
    symbol_dir = UPLOAD_DIR / "symbols"
    symbol_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.svg"
    (symbol_dir / filename).write_text(sanitized, encoding="utf-8")
    return f"symbols/{filename}"


def get_symbol(symbol_id):
    symbol = get_db().execute(
        """
        select symbols.*, symbol_categories.name as category_name,
               users.username as creator_name
        from symbols
        left join symbol_categories on symbol_categories.id = symbols.category_id
        left join users on users.id = symbols.created_by
        where symbols.id = ?
        """,
        (symbol_id,),
    ).fetchone()
    if symbol is None:
        abort(404)
    return symbol


def heraldry_definition(raw):
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as error:
        raise ValueError("Invalid heraldry design data.") from error
    fields = value.get("fields", {})
    if not isinstance(fields, dict) or not isinstance(value.get("elements", []), list):
        raise ValueError("Invalid heraldry design structure.")
    try:
        division = fields.get("division", "plain")
        if division not in {"plain", "per-pale", "per-fess", "quarterly"}:
            raise ValueError
        primary = str(fields.get("primary", "#ffffff"))
        secondary = str(fields.get("secondary", "#6b3080"))
        if not primary.startswith("#") or not secondary.startswith("#"):
            raise ValueError
        elements = []
        for element in value["elements"]:
            elements.append({
                "symbol_id": int(element["symbol_id"]),
                "x": max(0, min(100, float(element.get("x", 50)))),
                "y": max(0, min(100, float(element.get("y", 50)))),
                "width": max(1, min(100, float(element.get("width", 20)))),
                "height": max(1, min(100, float(element.get("height", 20)))),
                "rotation": float(element.get("rotation", 0)) % 360,
                "mirrored": bool(element.get("mirrored", False)),
                "layer_order": int(element.get("layer_order", 0)),
            })
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid decal field or element data: {error}") from error
    svg_markup = value.get("svg_markup", "")
    if svg_markup:
        try:
            svg_markup = sanitize_svg(svg_markup)
        except ValueError as error:
            raise ValueError(f"The edited SVG is invalid: {error}") from error
    source_template_id = value.get("source_template_id")
    try:
        source_template_id = int(source_template_id) if source_template_id is not None else None
    except (TypeError, ValueError) as error:
        raise ValueError("The source template reference is invalid.") from error
    return {"fields": {"division": division, "primary": primary, "secondary": secondary}, "elements": elements, "svg_markup": svg_markup, "source_template_id": source_template_id}


def heraldry_svg(design, template, elements):
    # Uploaded outlines retain their individual SVG areas. The editor marks each
    # selectable shape with data-fill and stores its chosen fill in the markup.
    if design.get("svg_markup"):
        return design["svg_markup"]
    if design.get("source_template_id"):
        source = get_db().execute("select svg_markup from decal_templates where id = ?", (design["source_template_id"],)).fetchone()
        if source:
            return source["svg_markup"]
    fields = design["fields"]
    template_definition = template["template_definition"] if "template_definition" in template.keys() else template["definition"]
    path = json.loads(template_definition)["path"]
    primary = fields["primary"]
    secondary = fields["secondary"]
    division = fields["division"]
    if division == "per-pale":
        field = f'<path d="{path}" fill="{primary}"/><path d="M 200 20 H 380 V 260 C 380 390 200 480 200 480 C 200 480 200 390 200 260 Z" fill="{secondary}"/>'
    elif division == "per-fess":
        field = f'<path d="{path}" fill="{primary}"/><path d="M 20 250 H 380 V 260 C 380 390 200 480 200 480 C 200 480 20 390 20 260 Z" fill="{secondary}"/>'
    elif division == "quarterly":
        field = f'<path d="{path}" fill="{primary}"/><path d="M 200 20 V 480 L 380 260 V 20 Z" fill="{secondary}"/>'
    else:
        field = f'<path d="{path}" fill="{primary}"/>'
    markup = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 500"><defs><clipPath id="shield-clip"><path d="{path}"/></clipPath></defs><g clip-path="url(#shield-clip)">{field}']
    for element in sorted(elements, key=lambda item: item["layer_order"]):
        symbol = get_db().execute("select * from symbols where id = ?", (element["symbol_id"],)).fetchone()
        version = get_db().execute("select svg_filename from symbol_versions where symbol_id = ? order by version_number desc limit 1", (element["symbol_id"],)).fetchone()
        if not symbol or not version:
            continue
        raw = (UPLOAD_DIR / version["svg_filename"]).read_text(encoding="utf-8")
        inner = raw[raw.find(">") + 1:raw.rfind("</svg>")]
        sx = -1 if element["mirrored"] else 1
        transform = f'translate({element["x"] * 4 - element["width"] * 2} {element["y"] * 5 - element["height"] * 2}) rotate({element["rotation"]} {element["width"] * 2} {element["height"] * 2}) scale({sx * element["width"] / 100} {element["height"] / 100})'
        markup.append(f'<g transform="{transform}">{inner}</g>')
    markup.append("</g></svg>")
    return "".join(markup)


def save_heraldry_svg(raw_svg):
    sanitized = sanitize_svg(raw_svg)
    return sanitized


def black_outline_svg(raw_svg):
    root = ET.fromstring(raw_svg)
    fillable_tags = {"path", "rect", "circle", "ellipse", "polygon", "polyline", "line"}

    def style_value(style, name):
        for declaration in style.split(";"):
            if ":" not in declaration:
                continue
            key, value = declaration.split(":", 1)
            if key.strip().lower() == name:
                return value.strip().lower()
        return None

    def is_black(value):
        return value in {"black", "#000", "#000000", "rgb(0,0,0)", "rgb(0, 0, 0)"}

    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1] if isinstance(element.tag, str) else ""
        if tag in fillable_tags:
            # Keep intentional solid-black artwork (such as a shield frame).
            # Other filled areas become transparent so they can be painted later.
            style = element.get("style", "")
            explicit_fill = style_value(style, "fill") or (element.get("fill") or "").strip().lower()
            explicit_stroke = style_value(style, "stroke") or (element.get("stroke") or "").strip().lower()
            keep_black_fill = tag != "line" and (
                is_black(explicit_fill)
                or (not explicit_fill and not explicit_stroke)
            )
            # Inline style declarations outrank SVG attributes in browsers; strip
            # their paint values before applying the normalized black scheme.
            retained = [part for part in style.split(";") if part.strip() and not part.strip().lower().startswith(("fill:", "stroke:", "stroke-width:"))]
            if retained:
                element.set("style", ";".join(retained))
            else:
                element.attrib.pop("style", None)
            element.set("fill", "#000000" if keep_black_fill else "none")
            element.set("stroke", "#000000")
            if not element.get("stroke-width"):
                element.set("stroke-width", "2")
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    return ET.tostring(root, encoding="unicode")


def svg_with_base_layer(raw_svg):
    """Remove legacy full-canvas paint layers before opening the vector editor."""
    root = ET.fromstring(raw_svg)
    for parent in root.iter():
        for child in list(parent):
            if child.attrib.get("id") == "decal-base-layer":
                parent.remove(child)
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    return ET.tostring(root, encoding="unicode")


def decode_svg_upload(raw_bytes):
    """Accept the UTF-8 and UTF-16 encodings commonly emitted by SVG editors."""
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            text = raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "<svg" in text.lower():
            return text
    raise ValueError("The file is not UTF-8 or UTF-16 SVG text, or it does not contain an <svg> element.")


def unique_decal_template_name(name):
    db = get_db()
    candidate = name
    suffix = 2
    while db.execute("select 1 from decal_templates where name = ?", (candidate,)).fetchone():
        candidate = f"{name} ({suffix})"
        suffix += 1
    return candidate


def user_lineage_rows(table):
    return get_db().execute(f"select * from {table} where owner_id = ? order by name", (g.user["id"],)).fetchall()


def ensure_lineage_wiki(page_type, title, owner_id):
    db = get_db()
    page = db.execute("select id from wiki_pages where page_type = ? and title = ? and created_by = ?", (page_type, title, owner_id)).fetchone()
    if page:
        return
    knowledge_id = create_knowledge_object(f"wiki_{page_type}", title, owner_id)
    slug = unique_wiki_slug(f"{title}-{page_type}-{owner_id}")
    cursor = db.execute("insert into wiki_pages (knowledge_object_id, slug, page_type, title, body, created_by) values (?, ?, ?, ?, ?, ?)", (knowledge_id, slug, page_type, title, f"{page_type.title()} record for {title}.", owner_id))
    db.execute("insert into wiki_revisions (page_id, revision_number, title, body, created_by) values (?, 1, ?, ?, ?)", (cursor.lastrowid, title, f"{page_type.title()} record for {title}.", owner_id))


def init_db():
    db = get_db()
    db.executescript(
        """
        create table if not exists users (
            id integer primary key autoincrement,
            username text not null unique,
            password_hash text not null,
            role text not null default 'collector',
            theme text not null default 'classic-dark',
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
    user_columns = [row["name"] for row in db.execute("pragma table_info(users)").fetchall()]
    if "theme" not in user_columns:
        db.execute("alter table users add column theme text not null default 'classic-dark'")
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
        "focal_x": "real",
        "focal_y": "real",
        "gallery_ratio_width": "integer not null default 1",
        "gallery_ratio_height": "integer not null default 1",
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
        # Pin the bootstrap administrator. Only this account may create further
        # accounts, even if additional administrators are added later.
        db.execute(
            "insert or ignore into settings (key, value) values ('bootstrap_admin_id', ?)",
            (str(first_admin["id"]),),
        )
    run_migrations(db)
    for row in db.execute("select id from items where item_uuid is null").fetchall():
        db.execute("update items set item_uuid=? where id=?", (str(uuid.uuid4()), row["id"]))
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
    if request.method == "GET" and request.args.get("advanced") in ("0", "1"):
        session["show_advanced_fields"] = request.args["advanced"] == "1"
    user_id = session.get("user_id")
    g.user = None
    if user_id:
        g.user = get_db().execute("select * from users where id = ?", (user_id,)).fetchone()
    if request.method == "POST" and request.blueprint != "scan_api":
        csrf_value = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
        if not valid_csrf_token(csrf_value):
            app.logger.warning(
                "CSRF validation failed: method=%s endpoint=%s host=%s form_token=%s session_token=%s session_cookie=%s",
                request.method,
                request.endpoint,
                request.host,
                bool(csrf_value),
                bool(session.get("csrf_token")),
                "session=" in request.headers.get("Cookie", ""),
            )
            if request.endpoint == "login":
                return clear_stale_login_session()
            abort(400)


@app.before_request
def retire_lineage_routes():
    if request.path == "/lineage" or request.path.startswith(("/people", "/houses")):
        abort(404)


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    if response.content_type.startswith("text/html"):
        response.headers.setdefault("Cache-Control", "no-store")
    if request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.errorhandler(413)
def request_entity_too_large(error):
    flash("The uploaded file is too large.", "error")
    return render_template("login.html" if g.user is None else "index.html"), 413


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


def is_bootstrap_admin(user):
    if user is None or user["role"] != "admin":
        return False
    configured_admin = get_db().execute(
        "select value from settings where key = 'bootstrap_admin_id'"
    ).fetchone()
    return configured_admin is not None and configured_admin["value"] == str(user["id"])


def bootstrap_admin_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        if not is_bootstrap_admin(g.user):
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


def clear_stale_login_session():
    session.clear()
    response = redirect(url_for("login"))
    cookie_name = app.config["SESSION_COOKIE_NAME"]
    response.delete_cookie(cookie_name, path="/")
    response.delete_cookie(cookie_name, path="/login")
    return response


@app.context_processor
def inject_security_helpers():
    advanced = bool(session.get("show_advanced_fields", False))
    args = request.args.to_dict(flat=False)
    args["advanced"] = ["0" if advanced else "1"]
    return {"csrf_token": csrf_token, "csrf_field": csrf_field, "has_capability": has_capability,
            "show_advanced_fields": advanced,
            "advanced_toggle_url": request.path + "?" + urlencode(args, doseq=True)}


def csrf_field():
    return f'<input type="hidden" name="csrf_token" value="{csrf_token()}">'


def local_redirect_target(target, fallback):
    if not target:
        return fallback
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith("/"):
        return fallback
    return target


def collection_return_url(collection_id):
    """Keep collection browsing context without accepting an external redirect."""
    fallback = url_for("collection_view", collection_id=collection_id)
    target = request.form.get("return_to") if request.method == "POST" else request.args.get("return_to")
    target = target or fallback
    if not target.startswith("/") or target.startswith("//") or len(target) > 4000 or "\\" in target or any(ord(char) < 32 for char in target):
        return fallback
    parsed = urlparse(target)
    if parsed.path != fallback:
        return fallback
    return parsed.path + ("?" + parsed.query if parsed.query else "")


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
        # One tile per item. The random gallery uses its first enabled image;
        # personal galleries keep the remaining images as slideshow frames.
        tile_key = item_id
        if mixed and tile_key in item_lookup:
            continue
        if tile_key not in item_lookup:
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
                "gallery_ratio_width": row["gallery_ratio_width"],
                "gallery_ratio_height": row["gallery_ratio_height"],
                "images": [],
            }
            item_lookup[tile_key] = item
            gallery_items.append(item)
        item_lookup[tile_key]["images"].append(row)
    if mixed:
        random.shuffle(gallery_items)
        return random_gallery_layout(gallery_items)
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


def random_gallery_layout(items, columns=6):
    """Randomize scale while preserving each photograph's ratio in grid units."""
    from math import gcd

    layout = []
    occupied = set()
    for item in items:
        tile = row_to_dict(item)
        ratio_width = tile.get("gallery_ratio_width", 1)
        ratio_height = tile.get("gallery_ratio_height", 1)
        if not 1 <= ratio_width <= 6 or not 1 <= ratio_height <= 6:
            raise ValueError("Gallery ratio must use whole numbers from 1 to 6.")
        divisor = gcd(ratio_width, ratio_height)
        unit_width, unit_height = ratio_width // divisor, ratio_height // divisor
        max_scale = min(columns // unit_width, 6 // unit_height, 3)
        if max_scale < 1:
            raise ValueError("Gallery ratio must fit within six grid units.")
        scale = random.choice(range(1, max_scale + 1))
        width, height = unit_width * scale, unit_height * scale
        x = y = 0
        while tile_overlaps(occupied, x, y, width, height):
            x += 1
            if x + width > columns:
                x = 0
                y += 1
        occupy_gallery_slot(occupied, x, y, width, height)
        tile.update(gallery_x=x, gallery_y=y, gallery_w=width, gallery_h=height)
        layout.append(tile)
    return layout


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


def form_field_definitions(form):
    """Return the non-empty field definitions submitted by the collection form."""
    return [(name, field_type) for _, name, field_type in form_collection_field_rows(form)]


def form_collection_field_rows(form):
    """Return submitted collection fields, including an ID for existing fields."""
    field_ids = form.getlist("field_id")
    field_names = form.getlist("field_name")
    field_types = form.getlist("field_type")
    if field_names:
        return [
            (
                field_ids[position].strip() if position < len(field_ids) else "",
                name.strip(),
                normalize_field_type(field_types[position] if position < len(field_types) else "text"),
            )
            for position, name in enumerate(field_names)
            if name.strip()
        ]
    return [
        ("", *parse_field_definition(field_line))
        for field_line in form.get("fields", "").splitlines()
        if parse_field_definition(field_line)[0]
    ]


def field_form_value(field, form):
    if field["field_type"] == "checkbox":
        return "true" if form.get(f"field_{field['id']}") else "false"
    value = form.get(f"field_{field['id']}", "").strip()
    if field["field_type"] == "dropdown" and value and value not in dropdown_fields.options(field):
        abort(400, description="Choose a configured dropdown option.")
    return value


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


def get_known_tags(owner_id):
    """Suggest tags used in this collector's collections without exposing others."""
    return [row["name"] for row in get_db().execute(
        """select distinct tags.name from tags
        join item_tags on item_tags.tag_id = tags.id
        join items on items.id = item_tags.item_id
        join collections on collections.id = items.collection_id
        where collections.owner_id = ? order by tags.name""", (owner_id,)
    )]


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
            record_activity("login", user_id=user["id"])
            get_db().commit()
            return redirect(local_redirect_target(request.args.get("next"), url_for("collections_index")))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/apply", methods=("GET", "POST"))
def apply():
    # Public registration is deliberately disabled. Administrators create
    # accounts from the protected Settings area instead.
    abort(404)


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
        select collections.*, users.username as owner_username,
            (select count(*) from items where items.collection_id = collections.id) as item_count,
            (select item_images.filename from item_images
             join items on items.id = item_images.item_id
             where items.collection_id = collections.id
             order by items.name, item_images.position, item_images.id limit 1) as preview_filename
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
    theme_choices = {
        "classic-dark": "Classic dark",
        "classic-light": "Classic light",
        "museum-dark": "Museum dark",
        "museum-light": "Museum light",
    }
    user = get_db().execute("select * from users where id = ?", (g.user["id"],)).fetchone()
    if request.method == "POST":
        if request.form.get("action") == "theme":
            theme = request.form.get("theme", "")
            if theme not in theme_choices:
                abort(400)
            get_db().execute("update users set theme = ? where id = ?", (theme, g.user["id"]))
            get_db().commit()
            flash("Theme updated.", "success")
            return redirect(url_for("user_settings"))
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
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
    return render_template(
        "user_settings.html",
        theme_choices=theme_choices,
        selected_theme=user["theme"] if user["theme"] in theme_choices else "classic-dark",
        can_create_accounts=is_bootstrap_admin(g.user),
    )


@app.route("/settings/accounts/new", methods=("GET", "POST"))
@bootstrap_admin_required
def create_account():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        role = normalize_role(request.form.get("role", "collector"))
        if not username:
            flash("Username is required.", "error")
        elif username_exists(username) or application_exists(username):
            flash("That username is already taken or awaiting approval.", "error")
        elif len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif password != confirm_password:
            flash("Passwords do not match.", "error")
        else:
            get_db().execute(
                "insert into users (username, password_hash, role) values (?, ?, ?)",
                (username, generate_password_hash(password), role),
            )
            get_db().commit()
            flash(f"Account created for {username}.", "success")
            return redirect(url_for("user_settings"))
    return render_template("create_account.html")


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
        ratios = []
        for key in ("gallery_ratio_width", "gallery_ratio_height"):
            try:
                value = int(request.form.get(key, image[key]))
            except ValueError:
                abort(400, description="Image aspect ratio must use whole numbers from 1 to 6.")
            if not 1 <= value <= 6:
                abort(400, description="Image aspect ratio must use whole numbers from 1 to 6.")
            ratios.append(value)
        focal_x, focal_y = image["focal_x"], image["focal_y"]
        if request.form.get("focal_mode") == "manual":
            import math
            try:
                focal_x = float(request.form["focal_x"])
                focal_y = float(request.form["focal_y"])
            except (KeyError, ValueError):
                abort(400, description="Choose a focal point within the image.")
            if not all(math.isfinite(value) and 0 <= value <= 100 for value in (focal_x, focal_y)):
                abort(400, description="Focal point coordinates must be between 0 and 100.")
        elif request.form.get("focal_mode") == "legacy":
            focal_x = focal_y = None
        elif "focal_mode" in request.form:
            abort(400, description="Choose a valid focal point mode.")
        get_db().execute(
            "update item_images set crop_x = ?, crop_y = ?, crop_zoom = ?, "
            "gallery_ratio_width = ?, gallery_ratio_height = ?, focal_x = ?, focal_y = ? where id = ?",
            (crop_x, crop_y, crop_zoom, *ratios, focal_x, focal_y, image["id"]),
        )
        get_db().commit()
        if is_background_request():
            return jsonify({"ok": True, "next": url_for("my_gallery")})
        flash("Image settings saved.", "success")
        if request.form.get("return_to_gallery"):
            return redirect(url_for("my_gallery"))
        return redirect(url_for("item_detail", item_id=image["item_id"]))
    if is_background_request():
        return jsonify({key: image[key] for key in (
            "crop_x", "crop_y", "crop_zoom", "gallery_ratio_width", "gallery_ratio_height", "focal_x", "focal_y"
        )})
    return render_template("image_crop.html", image=image)


@app.route("/images/<int:image_id>/delete", methods=("POST",))
@login_required
def delete_image(image_id):
    image = get_image(image_id)
    if not user_can_manage_image(image):
        abort(403)
    get_db().execute("delete from item_images where id = ?", (image["id"],))
    item = get_item(image["item_id"])
    capture_fields.refresh_summary(get_db(), item["id"], item["collection_id"])
    get_db().commit()
    try:
        (UPLOAD_DIR / image["filename"]).unlink()
    except FileNotFoundError:
        pass
    flash("Image deleted.", "success")
    return redirect(url_for("item_detail", item_id=image["item_id"]))


@app.route("/items/<int:item_id>/images/delete", methods=("POST",))
@login_required
def delete_selected_item_image(item_id):
    item = get_item(item_id)
    collection = get_collection(item["collection_id"])
    if not user_can_manage_collection(collection):
        abort(403)
    try:
        image_id = int(request.form.get("image_id", ""))
    except (TypeError, ValueError):
        abort(400)
    image = get_db().execute("select * from item_images where id = ? and item_id = ?", (image_id, item_id)).fetchone()
    if image is None:
        abort(404)
    get_db().execute("delete from item_images where id = ?", (image["id"],))
    item = get_item(image["item_id"])
    capture_fields.refresh_summary(get_db(), item["id"], item["collection_id"])
    get_db().commit()
    try:
        (UPLOAD_DIR / image["filename"]).unlink()
    except FileNotFoundError:
        pass
    if is_background_request():
        return jsonify({"ok": True})
    flash("Image deleted.", "success")
    return redirect(url_for("item_detail", item_id=item_id))


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
    if request.method == "POST":
        cursor = db.execute(
            "insert into collections (name, description, owner_id) values (?, ?, ?)",
            (request.form["name"].strip(), request.form.get("description", "").strip(), g.user["id"]),
        )
        for position, (_, field_name, field_type, options_json) in enumerate(dropdown_fields.specs(sys.modules[__name__], request.form)):
            db.execute(
                "insert into fields (collection_id, name, field_type, position, options_json) values (?, ?, ?, ?, ?)",
                (cursor.lastrowid, field_name, field_type, position, options_json),
            )
        db.commit()
        return redirect(url_for("collection_view", collection_id=cursor.lastrowid))
    return render_template(
        "collection_form.html",
        collection=None,
        field_type_choices=FIELD_TYPE_CHOICES,
    )


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
        for field_id, field_name, field_type, options_json in dropdown_fields.specs(sys.modules[__name__], request.form):
            if field_id:
                db.execute(
                    "update fields set name = ?, field_type = ?, options_json = ? where id = ? and collection_id = ?",
                    (field_name, field_type, options_json, field_id, collection["id"]),
                )
            else:
                db.execute(
                    "insert into fields (collection_id, name, field_type, position, options_json) values (?, ?, ?, ?, ?)",
                    (collection["id"], field_name, field_type, next_position, options_json),
                )
                next_position += 1
        db.commit()
        flash("Collection saved.", "success")
        return redirect(url_for("collection_view", collection_id=collection["id"]))
    return render_template(
        "collection_form.html",
        collection=collection,
        collectors=collectors,
        fields=get_fields(collection["id"]),
        field_type_choices=FIELD_TYPE_CHOICES,
    )


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
            or query in (item["item_uuid"] or "").lower()
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
            if field["field_type"] in ("checkbox", "dropdown"):
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
        if not request.form.get("name", "").strip():
            if is_background_request():
                return jsonify({"ok": False, "error": "Enter an item name."}), 400
            abort(400, description="Enter an item name.")
        db = get_db()
        cursor = db.execute(
            "insert into items (collection_id, name, description, item_uuid, rfid_uid) values (?, ?, ?, ?, ?)",
            (collection_id, request.form["name"].strip(), request.form.get("description", "").strip(), *scan_api.identity(request.form)),
        )
        item_id = cursor.lastrowid
        for field in fields:
            db.execute(
                "insert into item_values (item_id, field_id, value) values (?, ?, ?)",
                (item_id, field["id"], "" if field["capture_key"] else field_form_value(field, request.form)),
            )
        replace_item_tags(item_id, parse_tags(request.form.get("tags", "")))
        save_uploaded_images(item_id, request.files.getlist("images"))
        db.commit()
        next_url = url_for("item_detail", item_id=item_id, return_to=collection_return_url(collection["id"]))
        flash("Item saved.", "success")
        if is_background_request():
            return jsonify({"ok": True, "next": next_url})
        return redirect(next_url)
    return render_template(
        "item_form.html",
        collection=collection,
        return_to=collection_return_url(collection["id"]),
        fields=fields,
        field_suggestions=get_field_suggestions(collection_id),
        item=None,
        tags="",
        known_tags=get_known_tags(collection["owner_id"]),
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
    fields.sort(key=lambda field: (not bool(str(values.get(field["id"], "")).strip()), field["position"], field["id"]))
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
        return_to=collection_return_url(collection["id"]),
        fields=fields,
        values=values,
        images=images,
        tags=get_item_tags(item_id),
        captures={image["id"]: json.loads(image["capture_metadata"]) for image in images if image["capture_metadata"]} if can_manage else {},
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
        if not request.form.get("name", "").strip():
            if is_background_request():
                return jsonify({"ok": False, "error": "Enter an item name."}), 400
            abort(400, description="Enter an item name.")
        db.execute(
            "update items set name = ?, description = ?, item_uuid = ?, rfid_uid = ?, updated_at = current_timestamp where id = ?",
            (request.form["name"].strip(), request.form.get("description", "").strip(), *scan_api.identity(request.form), item_id),
        )
        for field in fields:
            if field["capture_key"] or (field["advanced"] and not session.get("show_advanced_fields")):
                continue
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
        next_url = url_for("item_detail", item_id=item_id, return_to=collection_return_url(collection["id"]))
        flash("Item saved.", "success")
        if is_background_request():
            return jsonify({"ok": True, "next": next_url})
        return redirect(next_url)
    values = {
        row["field_id"]: row["value"]
        for row in db.execute("select field_id, value from item_values where item_id = ?", (item_id,))
    }
    images = db.execute(
        "select * from item_images where item_id = ? "
        "order by gallery_enabled desc, gallery_position, position, id", (item_id,)
    ).fetchall()
    return render_template(
        "item_form.html",
        collection=collection,
        return_to=collection_return_url(collection["id"]),
        fields=fields,
        field_suggestions=get_field_suggestions(item["collection_id"]),
        item=item,
        values=values,
        tags=", ".join(get_item_tags(item_id)),
        known_tags=get_known_tags(collection["owner_id"]),
        images=images,
        captures={image["id"]: json.loads(image["capture_metadata"]) for image in images if image["capture_metadata"]},
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
        "insert into items (collection_id, name, description, item_uuid) values (?, ?, ?, ?)",
        (item["collection_id"], f"{item['name']} copy", item["description"], str(uuid.uuid4())),
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
    capture_fields.refresh_summary(db, new_item_id, item["collection_id"])
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


@app.route("/symbol-files/<path:filename>")
@login_required
def symbol_file(filename):
    if not filename.startswith("symbols/"):
        abort(404)
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/assets/<path:filename>")
def asset_file(filename):
    return send_from_directory(ASSET_DIR, filename)


@app.route("/symbols")
@login_required
def symbols_index():
    db = get_db()
    symbols = db.execute(
        "select symbols.*, symbol_categories.name as category_name from symbols left join symbol_categories on symbol_categories.id = symbols.category_id order by symbols.name"
    ).fetchall()
    return render_template("symbols.html", symbols=symbols)


@app.route("/symbols/new", methods=("GET", "POST"))
@capability_required("manage_symbols")
def new_symbol():
    db = get_db()
    categories = db.execute("select * from symbol_categories order by name").fetchall()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        raw_svg = request.form.get("svg", "").strip()
        if not name or not raw_svg:
            flash("Name and SVG are required.", "error")
        else:
            try:
                filename = save_symbol_svg(raw_svg)
            except ValueError as error:
                flash(str(error), "error")
            else:
                category_id = request.form.get("category_id") or None
                cursor = db.execute(
                    "insert into symbols (name, description, meaning, category_id, created_by) values (?, ?, ?, ?, ?)",
                    (name, request.form.get("description", "").strip(), request.form.get("meaning", "").strip(), category_id, g.user["id"]),
                )
                symbol_id = cursor.lastrowid
                db.execute(
                    "insert into symbol_versions (symbol_id, version_number, svg_filename, change_summary, created_by) values (?, 1, ?, ?, ?)",
                    (symbol_id, filename, "Initial version", g.user["id"]),
                )
                db.commit()
                flash("Symbol created.", "success")
                return redirect(url_for("symbol_detail", symbol_id=symbol_id))
    return render_template("symbol_form.html", symbol=None, categories=categories)


@app.route("/symbols/<int:symbol_id>")
@login_required
def symbol_detail(symbol_id):
    symbol = get_symbol(symbol_id)
    db = get_db()
    versions = db.execute("select * from symbol_versions where symbol_id = ? order by version_number desc", (symbol_id,)).fetchall()
    variants = db.execute("select * from symbol_variants where symbol_id = ? order by name", (symbol_id,)).fetchall()
    return render_template("symbol_detail.html", symbol=symbol, versions=versions, variants=variants)


@app.route("/symbols/<int:symbol_id>/edit", methods=("GET", "POST"))
@capability_required("manage_symbols")
def edit_symbol(symbol_id):
    symbol = get_symbol(symbol_id)
    db = get_db()
    categories = db.execute("select * from symbol_categories order by name").fetchall()
    if request.method == "POST":
        db.execute(
            "update symbols set name = ?, description = ?, meaning = ?, category_id = ?, updated_at = current_timestamp where id = ?",
            (request.form.get("name", "").strip(), request.form.get("description", "").strip(), request.form.get("meaning", "").strip(), request.form.get("category_id") or None, symbol_id),
        )
        raw_svg = request.form.get("svg", "").strip()
        if raw_svg:
            try:
                filename = save_symbol_svg(raw_svg)
            except ValueError as error:
                flash(str(error), "error")
                return render_template("symbol_form.html", symbol=symbol, categories=categories)
            version = db.execute("select coalesce(max(version_number), 0) + 1 from symbol_versions where symbol_id = ?", (symbol_id,)).fetchone()[0]
            db.execute(
                "insert into symbol_versions (symbol_id, version_number, svg_filename, change_summary, created_by) values (?, ?, ?, ?, ?)",
                (symbol_id, version, filename, request.form.get("change_summary", "").strip(), g.user["id"]),
            )
        db.commit()
        flash("Symbol saved.", "success")
        return redirect(url_for("symbol_detail", symbol_id=symbol_id))
    return render_template("symbol_form.html", symbol=symbol, categories=categories)


@app.route("/symbols/<int:symbol_id>/variants", methods=("POST",))
@capability_required("manage_symbols")
def add_symbol_variant(symbol_id):
    get_symbol(symbol_id)
    name = request.form.get("name", "").strip()
    if not name:
        flash("Variant name is required.", "error")
        return redirect(url_for("symbol_detail", symbol_id=symbol_id))
    filename = None
    raw_svg = request.form.get("svg", "").strip()
    if raw_svg:
        try:
            filename = save_symbol_svg(raw_svg)
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("symbol_detail", symbol_id=symbol_id))
    db = get_db()
    db.execute(
        "insert into symbol_variants (symbol_id, name, description, svg_filename, created_by) values (?, ?, ?, ?, ?)",
        (symbol_id, name, request.form.get("description", "").strip(), filename, g.user["id"]),
    )
    db.commit()
    flash("Symbol variant added.", "success")
    return redirect(url_for("symbol_detail", symbol_id=symbol_id))


@app.route("/heraldry")
@login_required
def heraldry_index():
    designs = []
    for row in get_db().execute("select heraldry_designs.*, heraldry_templates.name as template_name, heraldry_templates.definition as template_definition from heraldry_designs join heraldry_templates on heraldry_templates.id = heraldry_designs.template_id where heraldry_designs.created_by = ? order by heraldry_designs.name", (g.user["id"],)).fetchall():
        definition = json.loads(row["definition"])
        designs.append({"id": row["id"], "name": row["name"], "template_name": row["template_name"], "updated_at": row["updated_at"], "svg": heraldry_svg(definition, row, definition.get("elements", []))})
    return render_template("heraldry.html", designs=designs)


@app.route("/heraldry/new", methods=("GET", "POST"))
@capability_required("manage_heraldry")
def new_heraldry():
    db = get_db()
    designs = []
    for row in db.execute("select heraldry_designs.*, heraldry_templates.definition as template_definition from heraldry_designs join heraldry_templates on heraldry_templates.id = heraldry_designs.template_id where heraldry_designs.created_by = ? order by heraldry_designs.name", (g.user["id"],)).fetchall():
        definition = json.loads(row["definition"])
        designs.append({"id": row["id"], "name": row["name"], "svg": heraldry_svg(definition, row, definition.get("elements", []))})
    templates = db.execute("select * from decal_templates order by name").fetchall()
    return render_template("heraldry_form.html", design=None, designs=designs, templates=templates, initial_definition={"fields": {"primary": "#ffffff"}, "elements": [], "svg_markup": ""})


@app.route("/heraldry/upload", methods=("POST",))
@capability_required("manage_heraldry")
def upload_heraldry_outline():
    upload = request.files.get("svg_file")
    if not upload or not upload.filename.lower().endswith(".svg"):
        flash("Choose an SVG outline to upload.", "error")
        return redirect(url_for("new_heraldry"))
    try:
        markup = black_outline_svg(save_heraldry_svg(decode_svg_upload(upload.read())))
    except ValueError as error:
        flash(f"Could not import {upload.filename}: {error}", "error")
        return redirect(url_for("new_heraldry"))
    db = get_db()
    default_name = Path(upload.filename).stem or "Uploaded outline"
    name = request.form.get("name", "").strip() or default_name
    template_name = unique_decal_template_name(name)
    db.execute("insert into decal_templates (name, svg_markup, created_by) values (?, ?, ?)", (template_name, markup, g.user["id"]))
    template_id = db.execute("select id from decal_templates where name = ?", (template_name,)).fetchone()["id"]
    db.commit()
    flash(f"Template '{template_name}' added to the template library.", "success")
    if request.form.get("next") == "build":
        return redirect(url_for("create_decal_from_template", template_id=template_id))
    return redirect(url_for("decal_templates_index"))


@app.route("/decal-templates/new")
@capability_required("manage_heraldry")
def new_decal_template():
    return render_template("decal_template_form.html")


@app.route("/decal-templates")
@login_required
def decal_templates_index():
    templates = get_db().execute("select * from decal_templates order by name").fetchall()
    return render_template("decal_templates.html", templates=templates)


@app.route("/decal-templates/<int:template_id>/delete", methods=("POST",))
@capability_required("manage_heraldry")
def delete_decal_template(template_id):
    db = get_db()
    template = db.execute("select * from decal_templates where id = ?", (template_id,)).fetchone()
    if template is None:
        abort(404)
    db.execute("delete from decal_templates where id = ?", (template_id,))
    db.commit()
    flash(f"Template '{template['name']}' deleted. Existing decals created from it are unchanged.", "success")
    return redirect(url_for("decal_templates_index"))


@app.route("/decal-templates/delete-selected", methods=("POST",))
@capability_required("manage_heraldry")
def delete_selected_decal_templates():
    try:
        template_ids = {int(value) for value in request.form.getlist("template_ids")}
    except ValueError:
        abort(400)
    if template_ids:
        placeholders = ",".join("?" for _ in template_ids)
        get_db().execute(f"delete from decal_templates where id in ({placeholders})", tuple(template_ids))
        get_db().commit()
        flash(f"Deleted {len(template_ids)} template(s). Existing decals are unchanged.", "success")
    return redirect(url_for("decal_templates_index"))


@app.route("/decal-templates/<int:template_id>/create", methods=("GET", "POST"))
@capability_required("manage_heraldry")
def create_decal_from_template(template_id):
    db = get_db()
    source = db.execute("select * from decal_templates where id = ?", (template_id,)).fetchone()
    if source is None:
        abort(404)
    if request.method == "GET":
        return render_template("decal_from_template.html", template=source)
    shield = db.execute("select id from heraldry_templates order by id limit 1").fetchone()
    name = request.form.get("name", "").strip() or f"{source['name']} decal"
    definition = {"fields": {"division": "plain", "primary": "#ffffff", "secondary": "#000000"}, "elements": [], "svg_markup": source["svg_markup"], "source_template_id": source["id"]}
    knowledge_id = create_knowledge_object("heraldry", name, g.user["id"])
    cursor = db.execute("insert into heraldry_designs (knowledge_object_id, name, template_id, definition, created_by) values (?, ?, ?, ?, ?)", (knowledge_id, name, shield["id"], json.dumps(definition), g.user["id"]))
    db.execute("insert into heraldry_revisions (design_id, revision_number, snapshot, created_by) values (?, 1, ?, ?)", (cursor.lastrowid, json.dumps(definition), g.user["id"]))
    db.commit()
    return redirect(url_for("edit_heraldry", design_id=cursor.lastrowid))


def get_heraldry(design_id):
    design = get_db().execute("select heraldry_designs.*, heraldry_templates.name as template_name, heraldry_templates.definition as template_definition from heraldry_designs join heraldry_templates on heraldry_templates.id = heraldry_designs.template_id where heraldry_designs.id = ?", (design_id,)).fetchone()
    if design is None:
        abort(404)
    return design


@app.route("/heraldry/<int:design_id>/copy", methods=("POST",))
@capability_required("manage_heraldry")
def copy_heraldry(design_id):
    source = get_heraldry(design_id)
    if source["created_by"] != g.user["id"]:
        abort(404)
    db = get_db()
    name = (request.form.get("name") or f"Copy of {source['name']}").strip()
    knowledge_id = create_knowledge_object("heraldry", name, g.user["id"])
    cursor = db.execute("insert into heraldry_designs (knowledge_object_id, name, template_id, definition, created_by) values (?, ?, ?, ?, ?)", (knowledge_id, name, source["template_id"], source["definition"], g.user["id"]))
    db.execute("insert into heraldry_revisions (design_id, revision_number, snapshot, created_by) values (?, 1, ?, ?)", (cursor.lastrowid, source["definition"], g.user["id"]))
    db.commit()
    return redirect(url_for("edit_heraldry", design_id=cursor.lastrowid))


@app.route("/heraldry/<int:design_id>/delete", methods=("POST",))
@capability_required("manage_heraldry")
def delete_heraldry(design_id):
    design = get_heraldry(design_id)
    db = get_db()
    db.execute("delete from heraldry_designs where id = ?", (design_id,))
    db.commit()
    flash(f"Decal '{design['name']}' deleted. Its source SVG template remains in the library.", "success")
    return redirect(url_for("heraldry_index"))


@app.route("/heraldry/delete-selected", methods=("POST",))
@capability_required("manage_heraldry")
def delete_selected_heraldry():
    try:
        design_ids = {int(value) for value in request.form.getlist("design_ids")}
    except ValueError:
        abort(400)
    if design_ids:
        placeholders = ",".join("?" for _ in design_ids)
        get_db().execute(f"delete from heraldry_designs where id in ({placeholders}) and created_by = ?", (*design_ids, g.user["id"]))
        get_db().commit()
        flash(f"Deleted {len(design_ids)} decal(s).", "success")
    return redirect(url_for("heraldry_index"))


@app.route("/heraldry/<int:design_id>")
@login_required
def heraldry_detail(design_id):
    design = get_heraldry(design_id)
    definition = json.loads(design["definition"])
    svg = heraldry_svg(definition, design, definition["elements"])
    revisions = get_db().execute("select * from heraldry_revisions where design_id = ? order by revision_number desc", (design_id,)).fetchall()
    return render_template("heraldry_detail.html", design=design, definition=definition, svg=svg, revisions=revisions)


@app.route("/heraldry/<int:design_id>/edit", methods=("GET", "POST"))
@capability_required("manage_heraldry")
def edit_heraldry(design_id):
    design = get_heraldry(design_id)
    db = get_db()
    if request.method == "POST":
        try:
            definition = heraldry_definition(request.form.get("definition"))
        except (KeyError, ValueError) as error:
            flash(str(error), "error")
        else:
            revision = db.execute("select coalesce(max(revision_number), 0) + 1 from heraldry_revisions where design_id = ?", (design_id,)).fetchone()[0]
            db.execute("update heraldry_designs set name = ?, definition = ?, updated_at = current_timestamp where id = ?", (request.form.get("name", "").strip(), json.dumps(definition), design_id))
            db.execute("insert into heraldry_revisions (design_id, revision_number, snapshot, created_by) values (?, ?, ?, ?)", (design_id, revision, json.dumps(definition), g.user["id"]))
            db.commit()
            flash("Heraldry design saved.", "success")
            return redirect(url_for("heraldry_detail", design_id=design_id))
    initial_definition = json.loads(design["definition"])
    if not initial_definition.get("svg_markup"):
        initial_definition["svg_markup"] = heraldry_svg(initial_definition, design, initial_definition.get("elements", []))
    editor_svg = svg_with_base_layer(heraldry_svg(initial_definition, design, initial_definition.get("elements", [])))
    return render_template("heraldry_form.html", design=design, designs=[], initial_definition=initial_definition, editor_svg=editor_svg, is_new_upload=request.args.get("new_upload") == "1")


@app.route("/heraldry/<int:design_id>/export.svg")
@login_required
def export_heraldry_svg(design_id):
    design = get_heraldry(design_id)
    definition = json.loads(design["definition"])
    response = app.response_class(heraldry_svg(definition, design, definition["elements"]), mimetype="image/svg+xml")
    response.headers["Content-Disposition"] = f'attachment; filename="heraldry-{design_id}.svg"'
    return response


def get_person(person_id):
    person = get_db().execute("select * from people where id = ?", (person_id,)).fetchone()
    if person is None:
        abort(404)
    return person


def would_create_parent_cycle(parent_id, child_id):
    return get_db().execute(
        """with recursive descendants(id) as (
             select child_id from person_parents where parent_id = ?
             union all select person_parents.child_id from person_parents join descendants on person_parents.parent_id = descendants.id
           ) select 1 from descendants where id = ?""",
        (child_id, parent_id),
    ).fetchone() is not None


@app.route("/lineage")
@login_required
def lineage_index():
    db = get_db()
    people = db.execute("select people.*, group_concat(distinct heraldry_designs.name) as heraldry_names, max(wiki_pages.slug) as wiki_slug from people left join person_heraldry on person_heraldry.person_id = people.id left join heraldry_designs on heraldry_designs.id = person_heraldry.design_id left join wiki_pages on wiki_pages.page_type = 'person' and wiki_pages.title = people.name and wiki_pages.created_by = people.owner_id where people.owner_id = ? group by people.id order by people.name", (g.user["id"],)).fetchall()
    houses = db.execute("select houses.*, wiki_pages.slug as wiki_slug, count(person_house_memberships.person_id) as member_count from houses left join wiki_pages on wiki_pages.page_type = 'house' and wiki_pages.title = houses.name and wiki_pages.created_by = houses.owner_id left join person_house_memberships on person_house_memberships.house_id = houses.id where houses.owner_id = ? group by houses.id order by houses.name", (g.user["id"],)).fetchall()
    return render_template("lineage.html", people=people, houses=houses)


@app.route("/people/new", methods=("GET", "POST"))
@capability_required("manage_lineage")
def new_person():
    db = get_db()
    houses = user_lineage_rows("houses")
    people = user_lineage_rows("people")
    designs = db.execute("select id, name from heraldry_designs order by name").fetchall()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Person name is required.", "error")
        else:
            cursor = db.execute("insert into people (name, biography, birth_date, death_date, created_by, owner_id) values (?, ?, ?, ?, ?, ?)", (name, request.form.get("biography", "").strip(), request.form.get("birth_date", "").strip(), request.form.get("death_date", "").strip(), g.user["id"], g.user["id"]))
            person_id = cursor.lastrowid
            for house_id in request.form.getlist("house_ids"):
                db.execute("insert or ignore into person_house_memberships (person_id, house_id, role) values (?, ?, ?)", (person_id, int(house_id), request.form.get("house_role", "").strip()))
            for parent_id in request.form.getlist("parent_ids"):
                if int(parent_id) != person_id:
                    db.execute("insert or ignore into person_parents (parent_id, child_id) values (?, ?)", (int(parent_id), person_id))
            for design_id in request.form.getlist("heraldry_ids"):
                db.execute("insert or ignore into person_heraldry (person_id, design_id) values (?, ?)", (person_id, int(design_id)))
            ensure_lineage_wiki("person", name, g.user["id"])
            db.commit()
            return redirect(url_for("person_detail", person_id=person_id))
    return render_template("person_form.html", person=None, houses=houses, people=people, designs=designs, selected_houses=[], selected_parents=[], selected_designs=[])


@app.route("/people/<int:person_id>")
@login_required
def person_detail(person_id):
    person = get_person(person_id)
    if person["owner_id"] != g.user["id"]:
        abort(404)
    db = get_db()
    parents = db.execute("select people.* from people join person_parents on people.id = person_parents.parent_id where person_parents.child_id = ? order by people.name", (person_id,)).fetchall()
    children = db.execute("select people.* from people join person_parents on people.id = person_parents.child_id where person_parents.parent_id = ? order by people.name", (person_id,)).fetchall()
    partners = db.execute("select people.*, person_partners.relationship_type from people join person_partners on people.id = person_partners.partner_id where person_partners.person_id = ? order by people.name", (person_id,)).fetchall()
    houses = db.execute("select houses.*, person_house_memberships.role from houses join person_house_memberships on houses.id = person_house_memberships.house_id where person_house_memberships.person_id = ?", (person_id,)).fetchall()
    titles = db.execute("select titles.*, person_titles.start_date, person_titles.end_date from titles join person_titles on titles.id = person_titles.title_id where person_titles.person_id = ?", (person_id,)).fetchall()
    heraldry = db.execute("select heraldry_designs.*, person_heraldry.relationship_type from heraldry_designs join person_heraldry on heraldry_designs.id = person_heraldry.design_id where person_heraldry.person_id = ?", (person_id,)).fetchall()
    return render_template("person_detail.html", person=person, parents=parents, children=children, partners=partners, houses=houses, titles=titles, heraldry=heraldry, people=user_lineage_rows("people"), all_houses=user_lineage_rows("houses"), designs=db.execute("select id, name from heraldry_designs order by name").fetchall())


@app.route("/people/<int:person_id>/edit", methods=("GET", "POST"))
@capability_required("manage_lineage")
def edit_person(person_id):
    person = get_person(person_id)
    if person["owner_id"] != g.user["id"]:
        abort(404)
    if request.method == "POST":
        get_db().execute("update people set name = ?, biography = ?, birth_date = ?, death_date = ?, updated_at = current_timestamp where id = ?", (request.form.get("name", "").strip(), request.form.get("biography", "").strip(), request.form.get("birth_date", "").strip(), request.form.get("death_date", "").strip(), person_id))
        get_db().commit()
        flash("Person saved.", "success")
        return redirect(url_for("person_detail", person_id=person_id))
    db = get_db()
    return render_template("person_form.html", person=person, houses=user_lineage_rows("houses"), people=user_lineage_rows("people"), designs=db.execute("select id, name from heraldry_designs order by name").fetchall(), selected_houses=[row[0] for row in db.execute("select house_id from person_house_memberships where person_id = ?", (person_id,))], selected_parents=[row[0] for row in db.execute("select parent_id from person_parents where child_id = ?", (person_id,))], selected_designs=[row[0] for row in db.execute("select design_id from person_heraldry where person_id = ?", (person_id,))])


@app.route("/people/<int:person_id>/parents", methods=("POST",))
@capability_required("manage_lineage")
def add_person_parent(person_id):
    get_person(person_id)
    try:
        parent_id = int(request.form["parent_id"])
        get_person(parent_id)
    except (KeyError, ValueError):
        abort(400)
    if parent_id == person_id or would_create_parent_cycle(parent_id, person_id):
        flash("That relationship would create a family-tree cycle.", "error")
    else:
        get_db().execute("insert or ignore into person_parents (parent_id, child_id) values (?, ?)", (parent_id, person_id))
        get_db().commit()
        flash("Parent relationship saved.", "success")
    return redirect(url_for("person_detail", person_id=person_id))


@app.route("/people/<int:person_id>/relationships", methods=("POST",))
@capability_required("manage_lineage")
def add_person_relationship(person_id):
    person = get_person(person_id)
    if person["owner_id"] != g.user["id"]:
        abort(404)
    kind = request.form.get("kind")
    try:
        target_id = int(request.form["target_id"])
    except (KeyError, ValueError):
        abort(400)
    db = get_db()
    if kind == "partner":
        target = get_person(target_id)
        if target["owner_id"] != g.user["id"] or target_id == person_id:
            abort(400)
        db.execute("insert or ignore into person_partners (person_id, partner_id, relationship_type) values (?, ?, ?)", (person_id, target_id, request.form.get("relationship_type", "partner")))
        db.execute("insert or ignore into person_partners (person_id, partner_id, relationship_type) values (?, ?, ?)", (target_id, person_id, request.form.get("relationship_type", "partner")))
    elif kind == "house":
        if not db.execute("select 1 from houses where id = ? and owner_id = ?", (target_id, g.user["id"])).fetchone(): abort(400)
        db.execute("insert or ignore into person_house_memberships (person_id, house_id, role) values (?, ?, ?)", (person_id, target_id, request.form.get("relationship_type", "member")))
    elif kind == "heraldry":
        db.execute("insert or ignore into person_heraldry (person_id, design_id, relationship_type) values (?, ?, ?)", (person_id, target_id, request.form.get("relationship_type", "associated")))
    else:
        abort(400)
    db.commit()
    return redirect(url_for("person_detail", person_id=person_id))


@app.route("/houses/new", methods=("POST",))
@capability_required("manage_lineage")
def new_house():
    name = request.form.get("name", "").strip()
    if name:
        get_db().execute("insert or ignore into houses (name, description, created_by, owner_id) values (?, ?, ?, ?)", (name, request.form.get("description", "").strip(), g.user["id"], g.user["id"]))
        ensure_lineage_wiki("house", name, g.user["id"])
        get_db().commit()
        flash("House saved.", "success")
    return redirect(url_for("lineage_index"))


WIKI_PAGE_TYPES = ("general",)
WIKI_LINK_PATTERN = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
WIKI_TASK_PATTERN = re.compile(r"^\s*(?:[-*+]\s*)?\[([ xX])\]\s+(.+)$", re.MULTILINE)
WIKI_LIST_PATTERN = re.compile(r"^\s*[-*+]\s+")


def wiki_link_targets(body):
    db = get_db()
    pages = db.execute("select id, slug, title from wiki_pages").fetchall()
    by_slug = {page["slug"]: page for page in pages}
    by_title = {page["title"].casefold(): page for page in pages}
    targets = []
    for match in WIKI_LINK_PATTERN.finditer(body or ""):
        target = match.group(1).strip()
        page = by_slug.get(target) or by_title.get(target.casefold())
        if page:
            targets.append(page)
    return targets


def sync_wiki_page_links(page_id, body):
    db = get_db()
    db.execute("delete from wiki_page_links where source_page_id = ?", (page_id,))
    for target in {page["id"]: page for page in wiki_link_targets(body)}.values():
        if target["id"] != page_id:
            db.execute(
                "insert into wiki_page_links (source_page_id, target_page_id, relationship_type) values (?, ?, 'mentioned')",
                (page_id, target["id"]),
            )


def render_wiki_markdown(body):
    def replace_link(match):
        target = match.group(1).strip()
        label = (match.group(2) or target).strip()
        pages = wiki_link_targets(match.group(0))
        if pages:
            return f"[{label}]({url_for('wiki_page', slug=pages[0]['slug'])})"
        # Page-browser links use a canonical slug. Keep those usable even when
        # a page has been renamed after the link was copied.
        if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", target):
            return f"[{label}]({url_for('wiki_page', slug=target)})"
        return label

    source = WIKI_LINK_PATTERN.sub(replace_link, body or "")
    source = WIKI_TASK_PATTERN.sub(
        lambda match: f'<div class="wiki-task"><input type="checkbox" disabled{" checked" if match.group(1).lower() == "x" else ""}> {match.group(2)}</div>',
        source,
    )
    lines = source.splitlines()
    normalized = []
    previous_was_list = False
    for line in lines:
        is_list = bool(WIKI_LIST_PATTERN.match(line))
        if is_list and not previous_was_list and normalized and normalized[-1].strip():
            normalized.append("")
        if not is_list and previous_was_list and line.strip():
            normalized.append("")
        normalized.append(line)
        previous_was_list = is_list
    source = "\n".join(normalized)
    rendered = markdown(source, extensions=("extra", "sane_lists", "nl2br"))
    return bleach.clean(
        rendered,
        tags={"a", "abbr", "blockquote", "br", "code", "del", "div", "em", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "img", "input", "li", "ol", "p", "pre", "strong", "table", "tbody", "td", "th", "thead", "tr", "ul"},
        attributes={"a": ["href", "title"], "div": ["class"], "img": ["src", "alt", "title"], "input": ["type", "checked", "disabled"], "th": ["align"], "td": ["align"]},
        protocols={"http", "https", "mailto"},
        strip=True,
    )


def wiki_slug(title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "page"


def unique_wiki_slug(title, page_id=None):
    db = get_db()
    base = wiki_slug(title)
    candidate = base
    suffix = 2
    while db.execute("select 1 from wiki_pages where slug = ? and (? is null or id <> ?)", (candidate, page_id, page_id)).fetchone():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def get_wiki_page(slug):
    page = get_db().execute("select wiki_pages.*, users.username as creator_name from wiki_pages left join users on users.id = wiki_pages.created_by where wiki_pages.slug = ?", (slug,)).fetchone()
    if page is None:
        abort(404)
    return page


@app.route("/wiki")
@login_required
def wiki_index():
    query = request.args.get("q", "").strip()
    db = get_db()
    if query:
        pages = db.execute("select * from wiki_pages where title like ? or body like ? order by title", (f"%{query}%", f"%{query}%")).fetchall()
    else:
        pages = db.execute("select * from wiki_pages order by title").fetchall()
    return render_template("wiki.html", pages=pages, query=query)


@app.route("/wiki/new", methods=("GET", "POST"))
@capability_required("edit_wiki")
def new_wiki_page():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        if not title:
            flash("A title is required.", "error")
        else:
            db = get_db()
            body = request.form.get("body", "").strip()
            knowledge_id = create_knowledge_object("wiki_page", title, g.user["id"])
            slug = unique_wiki_slug(title)
            cursor = db.execute("insert into wiki_pages (knowledge_object_id, slug, page_type, title, body, created_by) values (?, ?, 'general', ?, ?, ?)", (knowledge_id, slug, title, body, g.user["id"]))
            page_id = cursor.lastrowid
            db.execute("insert into wiki_revisions (page_id, revision_number, title, body, created_by) values (?, 1, ?, ?, ?)", (page_id, title, body, g.user["id"]))
            sync_wiki_page_links(page_id, body)
            db.commit()
            return redirect(url_for("wiki_page", slug=slug))
    pages = get_db().execute("select slug, title from wiki_pages order by title").fetchall()
    return render_template("wiki_form.html", page=None, images=[], pages=pages)


@app.route("/wiki/<slug>")
@login_required
def wiki_page(slug):
    page = get_wiki_page(slug)
    db = get_db()
    links = db.execute("select wiki_pages.slug, wiki_pages.title, wiki_page_links.relationship_type from wiki_pages join wiki_page_links on wiki_pages.id = wiki_page_links.target_page_id where wiki_page_links.source_page_id = ? order by wiki_pages.title", (page["id"],)).fetchall()
    backlinks = db.execute("select wiki_pages.slug, wiki_pages.title, wiki_page_links.relationship_type from wiki_pages join wiki_page_links on wiki_pages.id = wiki_page_links.source_page_id where wiki_page_links.target_page_id = ? order by wiki_pages.title", (page["id"],)).fetchall()
    return render_template("wiki_page.html", page=page, rendered_body=render_wiki_markdown(page["body"]), links=links, backlinks=backlinks)


@app.route("/wiki/<slug>/edit", methods=("GET", "POST"))
@capability_required("edit_wiki")
def edit_wiki_page(slug):
    page = get_wiki_page(slug)
    db = get_db()
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        body = request.form.get("body", "").strip()
        revision = db.execute("select coalesce(max(revision_number), 0) + 1 from wiki_revisions where page_id = ?", (page["id"],)).fetchone()[0]
        new_slug = unique_wiki_slug(title, page["id"])
        db.execute("update wiki_pages set slug = ?, title = ?, body = ?, updated_at = current_timestamp where id = ?", (new_slug, title, body, page["id"]))
        db.execute("insert into wiki_revisions (page_id, revision_number, title, body, created_by) values (?, ?, ?, ?, ?)", (page["id"], revision, title, body, g.user["id"]))
        sync_wiki_page_links(page["id"], body)
        db.commit()
        flash("Wiki page saved.", "success")
        return redirect(url_for("wiki_page", slug=new_slug))
    images = db.execute("select * from wiki_images where page_id = ? order by id desc", (page["id"],)).fetchall()
    pages = db.execute("select slug, title from wiki_pages where id <> ? order by title", (page["id"],)).fetchall()
    return render_template("wiki_form.html", page=page, images=images, pages=pages)


@app.route("/wiki/<slug>/history")
@login_required
def wiki_history(slug):
    page = get_wiki_page(slug)
    revisions = get_db().execute("select wiki_revisions.*, users.username from wiki_revisions left join users on users.id = wiki_revisions.created_by where page_id = ? order by revision_number desc", (page["id"],)).fetchall()
    return render_template("wiki_history.html", page=page, revisions=revisions)


@app.route("/wiki/<slug>/images", methods=("POST",))
@capability_required("edit_wiki")
def upload_wiki_image(slug):
    page = get_wiki_page(slug)
    image = request.files.get("image")
    if image is None or not image.filename:
        flash("Choose an image to upload.", "error")
    elif not allowed_image(image.filename):
        flash("Choose a PNG, JPEG, GIF, WebP, or AVIF image.", "error")
    else:
        original_name = secure_filename(image.filename)
        extension = original_name.rsplit(".", 1)[1].lower()
        filename = f"{uuid.uuid4().hex}.{extension}"
        WIKI_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        image.save(WIKI_UPLOAD_DIR / filename)
        get_db().execute("insert into wiki_images (page_id, filename, original_name, uploaded_by) values (?, ?, ?, ?)", (page["id"], filename, original_name, g.user["id"]))
        get_db().commit()
        flash("Image uploaded. Copy its Markdown into the page.", "success")
    return redirect(url_for("edit_wiki_page", slug=slug))


@app.route("/wiki/images/<filename>")
@login_required
def wiki_image_file(filename):
    image = get_db().execute("select 1 from wiki_images where filename = ?", (filename,)).fetchone()
    if image is None:
        abort(404)
    return send_from_directory(WIKI_UPLOAD_DIR, filename)


ANNOTATION_TYPES = {"rectangle", "polygon", "ellipse", "point", "freehand"}
ANNOTATION_STATES = {"draft", "submitted", "approved", "rejected"}


def valid_annotation_geometry(geometry_type, raw_geometry):
    if geometry_type not in ANNOTATION_TYPES:
        raise ValueError("Unsupported annotation shape.")
    try:
        geometry = json.loads(raw_geometry or "{}")
    except json.JSONDecodeError as error:
        raise ValueError("Invalid annotation geometry.") from error
    if not isinstance(geometry, (dict, list)):
        raise ValueError("Invalid annotation geometry.")
    values = []
    def collect(value):
        if isinstance(value, (int, float)):
            values.append(value)
        elif isinstance(value, list):
            for child in value:
                collect(child)
        elif isinstance(value, dict):
            for child in value.values():
                collect(child)
    collect(geometry)
    if not values or any(value < 0 or value > 1 for value in values):
        raise ValueError("Annotation coordinates must be normalized between 0 and 1.")
    return geometry


@app.route("/images/<int:image_id>/annotate", methods=("GET", "POST"))
@capability_required("label_images")
def annotate_image(image_id):
    image = get_image(image_id)
    if not user_can_manage_image(image):
        abort(403)
    db = get_db()
    symbols = db.execute("select id, name from symbols order by name").fetchall()
    if request.method == "POST":
        try:
            geometry_type = request.form["geometry_type"]
            geometry = valid_annotation_geometry(geometry_type, request.form.get("geometry"))
            symbol_id = int(request.form["symbol_id"])
            if db.execute("select 1 from symbols where id = ?", (symbol_id,)).fetchone() is None:
                raise ValueError("Invalid symbol.")
        except (KeyError, ValueError) as error:
            flash(str(error), "error")
        else:
            cursor = db.execute("insert into annotations (image_id, symbol_id, geometry_type, geometry, labeler_id) values (?, ?, ?, ?, ?)", (image_id, symbol_id, geometry_type, json.dumps(geometry), g.user["id"]))
            annotation_id = cursor.lastrowid
            db.execute("insert into annotation_revisions (annotation_id, revision_number, geometry_type, geometry, symbol_id, status, created_by) values (?, 1, ?, ?, ?, 'draft', ?)", (annotation_id, geometry_type, json.dumps(geometry), symbol_id, g.user["id"]))
            db.commit()
            flash("Annotation saved as a draft.", "success")
            return redirect(url_for("annotate_image", image_id=image_id))
    annotations = db.execute("select annotations.*, symbols.name as symbol_name, users.username as labeler_name from annotations join symbols on symbols.id = annotations.symbol_id left join users on users.id = annotations.labeler_id where image_id = ? order by annotations.id", (image_id,)).fetchall()
    return render_template("annotate_image.html", image=image, symbols=symbols, annotations=annotations)


@app.route("/annotations/<int:annotation_id>/submit", methods=("POST",))
@capability_required("label_images")
def submit_annotation(annotation_id):
    annotation = get_db().execute("select * from annotations where id = ?", (annotation_id,)).fetchone()
    if annotation is None:
        abort(404)
    image = get_image(annotation["image_id"])
    if not user_can_manage_image(image) or annotation["status"] not in {"draft", "rejected"}:
        abort(403)
    get_db().execute("update annotations set status = 'submitted', updated_at = current_timestamp where id = ?", (annotation_id,))
    get_db().commit()
    flash("Annotation submitted for review.", "success")
    return redirect(url_for("annotate_image", image_id=image["id"]))


@app.route("/admin/annotations")
@capability_required("review_annotations")
def review_annotations():
    annotations = get_db().execute("select annotations.*, symbols.name as symbol_name, item_images.filename, items.name as item_name, users.username as labeler_name from annotations join symbols on symbols.id = annotations.symbol_id join item_images on item_images.id = annotations.image_id join items on items.id = item_images.item_id left join users on users.id = annotations.labeler_id where annotations.status = 'submitted' order by annotations.created_at").fetchall()
    return render_template("annotation_review.html", annotations=annotations)


@app.route("/admin/annotations/<int:annotation_id>/review", methods=("POST",))
@capability_required("review_annotations")
def review_annotation(annotation_id):
    decision = request.form.get("decision")
    if decision not in {"approved", "rejected"}:
        abort(400)
    db = get_db()
    annotation = db.execute("select * from annotations where id = ?", (annotation_id,)).fetchone()
    if annotation is None:
        abort(404)
    db.execute("update annotations set status = ?, reviewer_id = ?, updated_at = current_timestamp where id = ?", (decision, g.user["id"], annotation_id))
    db.execute("insert into annotation_reviews (annotation_id, reviewer_id, decision, note) values (?, ?, ?, ?)", (annotation_id, g.user["id"], decision, request.form.get("note", "").strip()))
    revision = db.execute("select coalesce(max(revision_number), 0) + 1 from annotation_revisions where annotation_id = ?", (annotation_id,)).fetchone()[0]
    db.execute("insert into annotation_revisions (annotation_id, revision_number, geometry_type, geometry, symbol_id, status, created_by) values (?, ?, ?, ?, ?, ?, ?)", (annotation_id, revision, annotation["geometry_type"], annotation["geometry"], annotation["symbol_id"], decision, g.user["id"]))
    record_audit(f"{decision}_annotation", "annotation", annotation_id)
    db.commit()
    flash(f"Annotation {decision}.", "success")
    return redirect(url_for("review_annotations"))


@app.route("/admin/ai")
@capability_required("admin_ai")
def ai_lab():
    db = get_db()
    datasets = db.execute("select ai_datasets.*, count(ai_dataset_versions.id) as version_count from ai_datasets left join ai_dataset_versions on ai_dataset_versions.dataset_id = ai_datasets.id group by ai_datasets.id order by ai_datasets.name").fetchall()
    runs = db.execute("select ai_training_runs.*, ai_datasets.name as dataset_name from ai_training_runs join ai_dataset_versions on ai_dataset_versions.id = ai_training_runs.dataset_version_id join ai_datasets on ai_datasets.id = ai_dataset_versions.dataset_id order by ai_training_runs.created_at desc limit 20").fetchall()
    models = db.execute("select ai_models.*, count(ai_model_versions.id) as version_count from ai_models left join ai_model_versions on ai_model_versions.model_id = ai_models.id group by ai_models.id order by ai_models.name").fetchall()
    model_versions = db.execute("select ai_model_versions.*, ai_models.name as model_name, exists(select 1 from ai_deployments where ai_deployments.model_version_id = ai_model_versions.id and ai_deployments.status = 'active') as active from ai_model_versions join ai_models on ai_models.id = ai_model_versions.model_id order by ai_models.name, version_number desc").fetchall()
    return render_template("ai_lab.html", datasets=datasets, runs=runs, models=models, model_versions=model_versions)


@app.route("/admin/ai/datasets", methods=("POST",))
@capability_required("admin_ai")
def create_ai_dataset():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Dataset name is required.", "error")
        return redirect(url_for("ai_lab"))
    db = get_db()
    cursor = db.execute("insert into ai_datasets (name, description, created_by) values (?, ?, ?)", (name, request.form.get("description", "").strip(), g.user["id"]))
    dataset_id = cursor.lastrowid
    annotations = db.execute("select annotations.id, annotations.symbol_id from annotations where annotations.status = 'approved' order by annotations.id").fetchall()
    version = db.execute("insert into ai_dataset_versions (dataset_id, version_number, sample_count, split_seed, created_by) values (?, 1, ?, ?, ?)", (dataset_id, len(annotations), 4217, g.user["id"]))
    version_id = version.lastrowid
    for index, annotation in enumerate(annotations):
        class_row = db.execute("select id from ai_classes where symbol_id = ?", (annotation["symbol_id"],)).fetchone()
        if class_row is None:
            class_row = db.execute("insert into ai_classes (name, symbol_id) select name, id from symbols where id = ?", (annotation["symbol_id"],))
            class_id = class_row.lastrowid
        else:
            class_id = class_row["id"]
        split = "test" if index % 10 == 0 else "validation" if index % 10 == 1 else "train"
        db.execute("insert into ai_dataset_samples (dataset_version_id, annotation_id, class_id, split) values (?, ?, ?, ?)", (version_id, annotation["id"], class_id, split))
    db.commit()
    flash("Dataset version created from approved annotations.", "success")
    return redirect(url_for("ai_lab"))


@app.route("/admin/ai/datasets/<int:version_id>/export")
@capability_required("admin_ai")
def export_ai_dataset(version_id):
    db = get_db()
    version = db.execute("select ai_dataset_versions.*, ai_datasets.name from ai_dataset_versions join ai_datasets on ai_datasets.id = ai_dataset_versions.dataset_id where ai_dataset_versions.id = ?", (version_id,)).fetchone()
    if version is None:
        abort(404)
    samples = db.execute("select ai_dataset_samples.*, annotations.geometry_type, annotations.geometry, annotations.image_id, ai_classes.name as class_name from ai_dataset_samples join annotations on annotations.id = ai_dataset_samples.annotation_id join ai_classes on ai_classes.id = ai_dataset_samples.class_id where dataset_version_id = ?", (version_id,)).fetchall()
    payload = {"dataset": version["name"], "version": version["version_number"], "splits": [dict(sample) for sample in samples]}
    response = app.response_class(json.dumps(payload, indent=2), mimetype="application/json")
    response.headers["Content-Disposition"] = f'attachment; filename="dataset-{version_id}.json"'
    return response


@app.route("/admin/ai/training", methods=("POST",))
@capability_required("admin_ai")
def create_training_run():
    version_id = request.form.get("dataset_version_id")
    if get_db().execute("select 1 from ai_dataset_versions where id = ?", (version_id,)).fetchone() is None:
        abort(400)
    try:
        parameters = valid_json_object(request.form.get("parameters"), "Training parameters must be valid JSON.")
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("ai_lab"))
    get_db().execute("insert into ai_training_runs (dataset_version_id, parameters, created_by) values (?, ?, ?)", (version_id, parameters, g.user["id"]))
    get_db().commit()
    flash("Training run queued.", "success")
    return redirect(url_for("ai_lab"))


@app.route("/admin/ai/models", methods=("POST",))
@capability_required("admin_ai")
def create_ai_model():
    name = request.form.get("name", "").strip()
    if name:
        get_db().execute("insert or ignore into ai_models (name, description) values (?, ?)", (name, request.form.get("description", "").strip()))
        get_db().commit()
        flash("Model registered.", "success")
    return redirect(url_for("ai_lab"))


@app.route("/admin/ai/models/<int:model_id>/versions", methods=("POST",))
@capability_required("admin_ai")
def add_ai_model_version(model_id):
    db = get_db()
    if db.execute("select 1 from ai_models where id = ?", (model_id,)).fetchone() is None:
        abort(404)
    version = db.execute("select coalesce(max(version_number), 0) + 1 from ai_model_versions where model_id = ?", (model_id,)).fetchone()[0]
    try:
        metrics = valid_json_object(request.form.get("metrics"), "Model metrics must be valid JSON.")
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("ai_lab"))
    db.execute("insert into ai_model_versions (model_id, version_number, metrics, created_by) values (?, ?, ?, ?)", (model_id, version, metrics, g.user["id"]))
    db.commit()
    flash("Model version imported.", "success")
    return redirect(url_for("ai_lab"))


@app.route("/admin/ai/models/versions/<int:version_id>/deploy", methods=("POST",))
@capability_required("deploy_models")
def deploy_ai_model(version_id):
    db = get_db()
    if db.execute("select 1 from ai_model_versions where id = ?", (version_id,)).fetchone() is None:
        abort(404)
    db.execute("update ai_deployments set status = 'rolled_back' where status = 'active'")
    db.execute("insert into ai_deployments (model_version_id, deployed_by) values (?, ?)", (version_id, g.user["id"]))
    record_audit("deploy_model", "ai_model_version", version_id)
    db.commit()
    flash("Model deployed.", "success")
    return redirect(url_for("ai_lab"))


@app.route("/images/<int:image_id>/suggestions", methods=("GET", "POST"))
@capability_required("label_images")
def image_suggestions(image_id):
    image = get_image(image_id)
    if not user_can_manage_image(image):
        abort(403)
    db = get_db()
    if request.method == "POST":
        model = db.execute("select ai_model_versions.id from ai_model_versions join ai_deployments on ai_deployments.model_version_id = ai_model_versions.id where ai_deployments.status = 'active' order by ai_deployments.deployed_at desc limit 1").fetchone()
        if model is None:
            flash("No active model is deployed.", "error")
        else:
            try:
                geometry_type = request.form["geometry_type"]
                geometry = valid_annotation_geometry(geometry_type, request.form.get("geometry"))
                symbol_id = int(request.form["symbol_id"])
                confidence = max(0, min(1, float(request.form.get("confidence", 0))))
            except (KeyError, ValueError) as error:
                flash(str(error), "error")
            else:
                db.execute("insert into ai_suggestions (image_id, model_version_id, symbol_id, geometry_type, geometry, confidence) values (?, ?, ?, ?, ?, ?)", (image_id, model["id"], symbol_id, geometry_type, json.dumps(geometry), confidence))
                db.commit()
                flash("AI suggestion created for review.", "success")
    suggestions = db.execute("select ai_suggestions.*, symbols.name as symbol_name from ai_suggestions join symbols on symbols.id = ai_suggestions.symbol_id where image_id = ? order by ai_suggestions.created_at desc", (image_id,)).fetchall()
    symbols = db.execute("select id, name from symbols order by name").fetchall()
    return render_template("suggestions.html", image=image, suggestions=suggestions, symbols=symbols)


def get_suggestion(suggestion_id):
    suggestion = get_db().execute("select * from ai_suggestions where id = ?", (suggestion_id,)).fetchone()
    if suggestion is None:
        abort(404)
    image = get_image(suggestion["image_id"])
    if not user_can_manage_image(image):
        abort(403)
    return suggestion, image


@app.route("/suggestions/<int:suggestion_id>/accept", methods=("POST",))
@capability_required("label_images")
def accept_suggestion(suggestion_id):
    suggestion, image = get_suggestion(suggestion_id)
    if suggestion["status"] != "pending":
        abort(400)
    db = get_db()
    cursor = db.execute("insert into annotations (image_id, symbol_id, geometry_type, geometry, status, labeler_id) values (?, ?, ?, ?, 'submitted', ?)", (image["id"], suggestion["symbol_id"], suggestion["geometry_type"], suggestion["geometry"], g.user["id"]))
    db.execute("insert into annotation_revisions (annotation_id, revision_number, geometry_type, geometry, symbol_id, status, created_by) values (?, 1, ?, ?, ?, 'submitted', ?)", (cursor.lastrowid, suggestion["geometry_type"], suggestion["geometry"], suggestion["symbol_id"], g.user["id"]))
    db.execute("update ai_suggestions set status = 'accepted', reviewed_by = ? where id = ?", (g.user["id"], suggestion_id))
    record_audit("accept_ai_suggestion", "ai_suggestion", suggestion_id)
    db.commit()
    flash("Suggestion accepted and submitted for annotation review.", "success")
    return redirect(url_for("image_suggestions", image_id=image["id"]))


@app.route("/suggestions/<int:suggestion_id>/reject", methods=("POST",))
@capability_required("label_images")
def reject_suggestion(suggestion_id):
    suggestion, image = get_suggestion(suggestion_id)
    if suggestion["status"] != "pending":
        abort(400)
    get_db().execute("update ai_suggestions set status = 'rejected', reviewed_by = ? where id = ?", (g.user["id"], suggestion_id))
    record_audit("reject_ai_suggestion", "ai_suggestion", suggestion_id)
    get_db().commit()
    flash("Suggestion rejected.", "success")
    return redirect(url_for("image_suggestions", image_id=image["id"]))


def record_activity(event_type, object_type="", object_id=None, metadata="{}", user_id=None):
    get_db().execute("insert into activity_events (user_id, event_type, object_type, object_id, metadata) values (?, ?, ?, ?, ?)", (user_id if user_id is not None else (g.user["id"] if g.user else None), event_type, object_type, object_id, metadata))


def record_audit(action, object_type="", object_id=None, details="{}", user_id=None):
    get_db().execute("insert into audit_events (user_id, action, object_type, object_id, details) values (?, ?, ?, ?, ?)", (user_id if user_id is not None else (g.user["id"] if g.user else None), action, object_type, object_id, details))


@app.route("/admin/metrics")
@admin_required
def metrics_dashboard():
    db = get_db()
    user_metrics = {
        "active_users": db.execute("select count(distinct user_id) from activity_events where created_at >= datetime('now', '-30 days')").fetchone()[0],
        "items_added": db.execute("select count(*) from items").fetchone()[0],
        "images_uploaded": db.execute("select count(*) from item_images").fetchone()[0],
        "heraldry_created": db.execute("select count(*) from heraldry_designs").fetchone()[0],
        "wiki_edits": db.execute("select count(*) from wiki_revisions").fetchone()[0],
    }
    label_counts = db.execute("select status, count(*) as count from annotations group by status order by status").fetchall()
    ai_metrics = {
        "supported_classes": db.execute("select count(*) from ai_classes").fetchone()[0],
        "labels_total": db.execute("select count(*) from annotations").fetchone()[0],
        "review_backlog": db.execute("select count(*) from annotations where status = 'submitted'").fetchone()[0],
        "classes_needing_samples": db.execute("select count(*) from ai_classes left join annotations on annotations.symbol_id = ai_classes.symbol_id and annotations.status = 'approved' group by ai_classes.id having count(annotations.id) < 5").fetchall(),
        "training_runs": db.execute("select count(*) from ai_training_runs").fetchone()[0],
        "model_versions": db.execute("select count(*) from ai_model_versions").fetchone()[0],
        "suggestions": db.execute("select count(*) from ai_suggestions").fetchone()[0],
        "suggestions_accepted": db.execute("select count(*) from ai_suggestions where status = 'accepted'").fetchone()[0],
    }
    suggestion_total = ai_metrics["suggestions"]
    ai_metrics["suggestion_acceptance_rate"] = round(ai_metrics["suggestions_accepted"] * 100 / suggestion_total, 1) if suggestion_total else 0
    recent_audit = db.execute("select audit_events.*, users.username from audit_events left join users on users.id = audit_events.user_id order by audit_events.created_at desc limit 30").fetchall()
    return render_template("metrics.html", user_metrics=user_metrics, ai_metrics=ai_metrics, label_counts=label_counts, recent_audit=recent_audit)


@app.route("/admin/symbol-categories", methods=("POST",))
@admin_required
def create_symbol_category():
    name = request.form.get("name", "").strip()
    if name:
        get_db().execute("insert or ignore into symbol_categories (name, description) values (?, ?)", (name, request.form.get("description", "").strip()))
        get_db().commit()
        flash("Symbol category saved.", "success")
    return redirect(url_for("symbols_index"))


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
@bootstrap_admin_required
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
        if not is_bootstrap_admin(g.user):
            abort(403)
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
    capability_rows = db.execute(
        "select user_id, capability from user_capabilities where enabled = 1"
    ).fetchall()
    user_capabilities = {}
    for row in capability_rows:
        user_capabilities.setdefault(row["user_id"], set()).add(row["capability"])
    return render_template(
        "admin_users.html",
        users=users,
        capabilities=CAPABILITIES,
        user_capabilities=user_capabilities,
        can_create_accounts=is_bootstrap_admin(g.user),
    )


@app.route("/admin/users/<int:user_id>/capabilities", methods=("POST",))
@admin_required
def update_user_capabilities(user_id):
    db = get_db()
    user = db.execute("select id from users where id = ?", (user_id,)).fetchone()
    if user is None:
        abort(404)
    selected = {value for value in request.form.getlist("capabilities") if value in CAPABILITIES}
    db.execute("delete from user_capabilities where user_id = ?", (user_id,))
    db.executemany(
        "insert into user_capabilities (user_id, capability) values (?, ?)",
        ((user_id, capability) for capability in selected),
    )
    db.commit()
    flash("User capabilities saved.", "success")
    return redirect(url_for("admin_users"))


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
                "insert into fields (collection_id, name, field_type, position, options_json) values (?, ?, ?, ?, ?)",
                (selected_id, name, field_type, position, dropdown_fields.encode(request.form.get("field_options", ""), field_type)),
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
            "update fields set name = ?, field_type = ?, position = ?, options_json = ? where id = ?",
            (name, field_type, position, dropdown_fields.encode(request.form.get("field_options", ""), field_type, db, field["id"]), field["id"]),
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


api_keys.register(sys.modules[__name__])
scan_api.register(sys.modules[__name__])
dropdown_fields.register(sys.modules[__name__])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=os.environ.get("FLASK_DEBUG") == "1")
