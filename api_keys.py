"""Account-owned API credentials. Raw keys are displayed once, never stored."""

import hashlib
import secrets
from functools import wraps

from flask import Blueprint, g, jsonify, redirect, render_template, request, url_for


def migrate(db):
    db.execute("""create table api_keys (
        id integer primary key, user_id integer not null references users(id) on delete cascade,
        name text not null, prefix text not null, key_hash text not null unique,
        created_at text not null default current_timestamp, last_used_at text,
        revoked_at text)""")


def digest(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def register(host):
    bp = Blueprint("api_keys", __name__)

    @bp.route("/settings/api-keys", methods=["GET", "POST"])
    @host.login_required
    def settings():
        db = host.get_db()
        raw = None
        error = None
        if request.method == "POST":
            db.execute("begin immediate")
            if request.form.get("action") == "revoke":
                db.execute(
                    "update api_keys set revoked_at=current_timestamp where id=? and user_id=? and revoked_at is null",
                    (request.form.get("key_id"), g.user["id"]),
                )
                db.commit()
                return redirect(url_for("api_keys.settings"))
            count = db.execute(
                "select count(*) from api_keys where user_id=? and revoked_at is null",
                (g.user["id"],),
            ).fetchone()[0]
            name = request.form.get("name", "").strip()
            if count >= 5:
                error = "You may have up to five active API keys. Revoke one before creating another."
            elif not name or len(name) > 80:
                error = "Enter a key name of 1–80 characters."
            else:
                raw = "mg_" + secrets.token_urlsafe(32)
                db.execute(
                    "insert into api_keys(user_id,name,prefix,key_hash) values(?,?,?,?)",
                    (g.user["id"], name, raw[:11], digest(raw)),
                )
            db.commit()
        keys = db.execute(
            "select id,name,prefix,created_at,last_used_at from api_keys where user_id=? and revoked_at is null order by id desc",
            (g.user["id"],),
        ).fetchall()
        return render_template("api_keys.html", keys=keys, new_key=raw, error=error), (
            400 if error else 200
        )

    host.app.register_blueprint(bp)


def authenticated(host):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer ") or len(header) > 256:
                return jsonify(error="A valid Bearer API key is required."), 401
            db = host.get_db()
            row = db.execute(
                """select users.*, api_keys.id as credential_id from api_keys
                join users on users.id=api_keys.user_id
                where key_hash=? and revoked_at is null""",
                (digest(header[7:]),),
            ).fetchone()
            if row is None:
                return jsonify(error="Invalid or revoked API key."), 401
            g.user = row
            db.execute(
                "update api_keys set last_used_at=current_timestamp where id=?",
                (row["credential_id"],),
            )
            db.commit()
            return view(*args, **kwargs)

        return wrapped

    return decorator
