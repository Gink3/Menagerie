"""Canonical optional Advanced fields and validated per-photo acquisition records."""

import json
import math
from datetime import datetime, timezone

FIELDS = (
    ("light_enabled", "Capture: Light On", "checkbox"),
    ("light_brightness", "Capture: Light Brightness (%)", "number"),
    ("light_frequency", "Capture: PWM Frequency (Hz)", "number"),
    ("camera", "Capture: Camera", "text"),
    ("resolution", "Capture: Resolution", "text"),
    ("captured_at", "Capture: Captured At", "timestamp"),
    ("focus_score", "Capture: Focus Score", "number"),
    ("focus_method", "Capture: Focus Method", "text"),
)


def migrate(db):
    db.execute("alter table fields add column advanced integer not null default 0")
    db.execute("alter table fields add column capture_key text")
    db.execute("create unique index fields_capture_key on fields(collection_id,capture_key) where capture_key is not null")
    db.execute("alter table item_images add column capture_metadata text")


def ensure(db, collection_id):
    fields = [dict(row) for row in db.execute("select * from fields where collection_id=?", (collection_id,))]
    planned = []
    for key, name, kind in FIELDS:
        found = [f for f in fields if f["capture_key"] == key or f["name"].casefold() == name.casefold()]
        if len(found) > 1 or (found and (found[0]["field_type"] != kind or found[0]["name"] != name or found[0]["capture_key"] not in (None, key))):
            raise ValueError(f'Capture field "{name}" must be unique and have type "{kind}". Edit the collection before uploading.')
        planned.append((key, name, kind, found[0] if found else None))
    position = max([f["position"] for f in fields] or [-1]) + 1
    for key, name, kind, found in planned:
        if found:
            db.execute("update fields set advanced=1,capture_key=? where id=?", (key, found["id"]))
        else:
            db.execute("insert into fields(collection_id,name,field_type,position,advanced,capture_key) values(?,?,?,?,1,?)",
                       (collection_id, name, kind, position, key))
            position += 1


def integer(value, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError("Invalid capture numeric setting.")
    return value


def validate(records, count):
    if records is None:
        return [None] * count
    if not isinstance(records, list) or len(records) != count:
        raise ValueError("Capture records must match the ordered images, including null for imported photos.")
    normalized = []
    for record in records:
        if record is None:
            normalized.append(None)
            continue
        if not isinstance(record, dict) or type(record.get("version")) is not int or record.get("version") != 1:
            raise ValueError("Unsupported capture record version.")
        captured = datetime.fromisoformat(record["captured_at"].replace("Z", "+00:00"))
        if captured.tzinfo is None:
            raise ValueError("Capture timestamp must include its timezone.")
        camera = record["camera"]
        if not isinstance(camera, str) or not 1 <= len(camera) <= 500:
            raise ValueError("Invalid capture camera name.")
        width, height = integer(record["width"], 1, 40000000), integer(record["height"], 1, 40000000)
        if width * height > 40000000:
            raise ValueError("Capture dimensions exceed 40 megapixels.")
        result = dict(version=1, captured_at=captured.astimezone(timezone.utc).isoformat(), camera=camera,
                      width=width, height=height)
        if "simulated_camera" in record:
            if not isinstance(record["simulated_camera"], bool):
                raise ValueError("Invalid simulated camera flag.")
            result["simulated_camera"] = record["simulated_camera"]
        light = record.get("lighting")
        if light is not None:
            if not isinstance(light, dict) or not isinstance(light.get("enabled"), bool):
                raise ValueError("Invalid capture light state.")
            result["lighting"] = dict(enabled=light["enabled"], brightness=integer(light.get("brightness"), 0, 100),
                frequency=integer(light.get("frequency"), 1, 100000), gpio=integer(light.get("gpio"), 0, 29))
            if "simulated" in light:
                if not isinstance(light["simulated"], bool):
                    raise ValueError("Invalid simulated lighting flag.")
                result["lighting"]["simulated"] = light["simulated"]
            result["lighting"]["duty_u16"] = round(light["brightness"] * 65535 / 100) if light["enabled"] else 0
        focus = record.get("focus")
        if focus is not None:
            if not isinstance(focus, dict) or not isinstance(focus.get("method"), str) or not 1 <= len(focus["method"]) <= 100:
                raise ValueError("Invalid capture focus method.")
            score = focus.get("score")
            if score is not None and (isinstance(score, bool) or not isinstance(score, (float, int)) or not math.isfinite(score) or score < 0):
                raise ValueError("Invalid capture focus score.")
            roi = focus.get("roi")
            if not isinstance(roi, list) or len(roi) != 4 or not all(isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= 1 for v in roi) or not (roi[0] < roi[2] and roi[1] < roi[3]):
                raise ValueError("Invalid focus region.")
            result["focus"] = dict(method=focus["method"], score=score, roi=roi)
        normalized.append(result)
    return normalized


def refresh_summary(db, item_id, collection_id):
    rows = db.execute("select capture_metadata from item_images where item_id=? and capture_metadata is not null", (item_id,))
    records = [json.loads(row["capture_metadata"]) for row in rows]
    values = {}
    if records:
        latest = max(records, key=lambda r: datetime.fromisoformat(r["captured_at"]))
        values.update(camera=latest["camera"], resolution=f"{latest['width']} × {latest['height']}", captured_at=latest["captured_at"])
        light = latest.get("lighting")
        if light is not None:
            values.update(light_enabled="true" if light["enabled"] else "false", light_brightness=str(light["brightness"]), light_frequency=str(light["frequency"]))
        focus = latest.get("focus")
        if focus:
            values.update(focus_method=focus["method"], focus_score=str(focus["score"]) if focus["score"] is not None else "")
    for field in db.execute("select id,capture_key from fields where collection_id=? and capture_key is not null", (collection_id,)):
        db.execute("insert into item_values(item_id,field_id,value) values(?,?,?) on conflict(item_id,field_id) do update set value=excluded.value",
                   (item_id, field["id"], values.get(field["capture_key"], "")))
