"""Atomic local drafts and finalized images, isolated by server and account."""

import hashlib
import json
import math
import re
from datetime import date, datetime
import os
import shutil
import time
import uuid
from pathlib import Path
from PIL import Image, ImageOps

DATA = (
    Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    / "mini_scan"
)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


class Store:
    def __init__(self, root=DATA):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def folder(self, server, user):
        account = hashlib.sha256(f"{server}|{user}".encode()).hexdigest()[:24]
        folder = self.root / "drafts" / account
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def cache_account(self, server, user, collections):
        write_json(
            self.folder(server, user["id"]) / "account.json",
            dict(
                server=server, user=user, collections=collections, cached_at=time.time()
            ),
        )

    def cached_accounts(self, server):
        accounts = []
        for path in (self.root / "drafts").glob("*/account.json"):
            try:
                data = json.loads(path.read_text())
                if data["server"] == server:
                    accounts.append(data)
            except (OSError, ValueError, KeyError):
                continue
        return accounts

    def drafts(self, server, user):
        return [
            Draft(p.parent)
            for p in sorted(self.folder(server, user).glob("*/draft.json"))
        ]

    def for_tag(self, server, user, uid):
        if not uid:
            return None
        return next(
            (d for d in self.drafts(server, user) if d.data["rfid_uid"] == uid), None
        )

    def create(self, server, user, collection, uid=None):
        existing = self.for_tag(server, user, uid)
        if existing:
            return existing
        path = self.folder(server, user) / str(uuid.uuid4())
        path.mkdir()
        data = dict(
            version=1,
            server=server,
            user_id=user,
            collection_id=collection["id"],
            schema=collection,
            item_uuid=str(uuid.uuid4()),
            rfid_uid=uid,
            name="",
            description="",
            tags="",
            values={},
            images=[],
            status="draft",
            created_at=time.time(),
            updated_at=time.time(),
            request_id=None,
            result=None,
        )
        write_json(path / "draft.json", data)
        return Draft(path)

    def recapture(self, server, user, collection, item):
        # Multiple completed sessions may exist for a tag; reuse only pending work.
        pending = next(
            (
                d
                for d in self.drafts(server, user)
                if d.data.get("target_item_id") == item["id"]
                and d.data["status"] != "uploaded"
            ),
            None,
        )
        if pending:
            return pending
        draft = self.create(server, user, collection)
        draft.data.update(
            target_item_id=item["id"],
            item_uuid=item["item_uuid"],
            rfid_uid=item.get("rfid_uid"),
            name=item["name"],
            image_mode="append",
        )
        draft.save()
        return draft

    def usage(self):
        return sum(
            p.stat().st_size for p in (self.root / "drafts").rglob("*") if p.is_file()
        )

    def prune_uploaded(self, limit_bytes, protected=()):
        protected = {Path(p) for p in protected}
        candidates = []
        for path in (self.root / "drafts").glob("*/*/draft.json"):
            draft = Draft(path.parent)
            if draft.data["status"] == "uploaded" and draft.path not in protected:
                candidates.append(draft)
        removed = 0
        for draft in sorted(candidates, key=lambda d: d.data["updated_at"]):
            if self.usage() <= limit_bytes:
                break
            shutil.rmtree(draft.path)
            removed += 1
        return removed


class Draft:
    @property
    def key(self):
        return self.data.get("rfid_uid") or self.data["item_uuid"]

    def __init__(self, path):
        self.path = Path(path)
        self.data = json.loads((self.path / "draft.json").read_text())

    def save(self):
        self.data["updated_at"] = time.time()
        write_json(self.path / "draft.json", self.data)

    def mutable(self):
        if self.data["status"] in ("sending", "uncertain", "conflict", "uploaded"):
            raise ValueError(
                "This submission is frozen. Retry its upload or edit the uploaded item in Menagerie."
            )

    def add_image(self, source):
        self.mutable()
        with Image.open(source) as image:
            if image.width * image.height > 40_000_000:
                raise ValueError("Image exceeds 40 megapixels.")
            image = ImageOps.exif_transpose(image).convert("RGB")
            name = uuid.uuid4().hex + ".jpg"
            image.save(self.path / name, quality=95)
        originals = self.path / "originals"
        originals.mkdir(exist_ok=True)
        original = "originals/" + uuid.uuid4().hex + Path(source).suffix.lower()
        shutil.copyfile(source, self.path / original)
        self.data["images"].append(dict(file=name, original=original, selected=True))
        self.save()

    def edit(self, index, box=None, size=None, adjustments=None):
        self.mutable()
        old = self.data["images"][index]["file"]
        with Image.open(self.path / old) as image:
            if box:
                left, top, right, bottom = map(int, box)
                if not (
                    0 <= left < right <= image.width
                    and 0 <= top < bottom <= image.height
                ):
                    raise ValueError("Crop must be inside the image.")
                image = image.crop((left, top, right, bottom))
            if size:
                width, height = map(int, size)
                if width < 1 or height < 1 or width * height > 40_000_000:
                    raise ValueError("Choose positive dimensions up to 40 megapixels.")
                image = image.resize((width, height), Image.Resampling.LANCZOS)
            if adjustments:
                from .editing import adjust
                image = adjust(image, **adjustments)
            name = uuid.uuid4().hex + ".jpg"
            image.save(self.path / name, quality=95)
        self.data["images"][index].setdefault("edits", []).append(dict(crop=box, size=size, adjustments=adjustments or {}))
        self.data["images"][index]["file"] = name
        self.save()
        (self.path / old).unlink(missing_ok=True)

    def restore_original(self, index):
        self.mutable()
        entry = self.data["images"][index]
        if not entry.get("original"):
            raise ValueError("This older photo has no preserved original.")
        with Image.open(self.path / entry["original"]) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            name = uuid.uuid4().hex + ".jpg"
            image.save(self.path / name, quality=95)
        old = entry["file"]
        entry["file"] = name
        entry["edits"] = []
        self.save()
        (self.path / old).unlink(missing_ok=True)

    def delete_image(self, index):
        self.mutable()
        entry = self.data["images"].pop(index)
        self.save()
        (self.path / entry["file"]).unlink(missing_ok=True)
        if entry.get("original"):
            (self.path / entry["original"]).unlink(missing_ok=True)

    def submission(self):
        d = self.data
        payload = {
            k: d[k]
            for k in ("item_uuid", "rfid_uid", "name", "description", "tags", "values")
        }

        selected = [i for i in d["images"] if i["selected"]]
        if any(i.get("capture") for i in selected):
            payload["captures"] = [i.get("capture") for i in selected]
        if d.get("target_item_id"):
            payload["image_mode"] = d.get("image_mode", "append")
        return payload

    def recover_conflict(self):
        if self.data["status"] != "conflict":
            raise ValueError("Only a rejected conflict can start a new item.")
        self.data.setdefault("previous_submissions", []).append(
            {
                "item_uuid": self.data["item_uuid"],
                "rfid_uid": self.data["rfid_uid"],
                "request_id": self.data["request_id"],
                "conflict": self.data.get("conflict"),
            }
        )
        self.data.update(
            item_uuid=str(uuid.uuid4()),
            rfid_uid=None,
            request_id=None,
            status="draft",
            result=None,
            conflict=None,
            target_item_id=None,
            lookup_pending=False,
        )
        self.save()

    def validate(self):
        d = self.data
        limits = d["schema"].get("upload_limits", {})
        if not d["name"].strip() or len(d["name"]) > limits.get("max_name_length", 500):
            raise ValueError("Enter a name of up to 500 characters.")
        selected = [i for i in d["images"] if i["selected"]]
        if not 1 <= len(selected) <= limits.get("max_images", 30):
            raise ValueError(
                f"Select between 1 and {limits.get('max_images', 30)} photos."
            )
        for entry in selected:
            with Image.open(self.path / entry["file"]) as image:
                if image.width * image.height > limits.get(
                    "max_image_pixels", 40_000_000
                ):
                    raise ValueError("Image exceeds the server's pixel limit.")
        if not d.get("target_item_id"):
            if d["schema"].get("issues"):
                raise ValueError(
                    "Collection needs edits: " + "\n".join(d["schema"]["issues"])
                )
            fields = d["schema"]["fields"]
            if set(d["values"]) - {str(f["id"]) for f in fields}:
                raise ValueError("Collection fields changed. Refresh collections.")
            for field in fields:
                value = d["values"].get(str(field["id"]), "")
                kind = field["field_type"]
                if kind == "checkbox":
                    if value not in (True, False, "true", "false", ""):
                        raise ValueError(f"{field['name']}: expected a checkbox value.")
                    continue
                if not isinstance(value, str):
                    raise ValueError(f"{field['name']}: expected text.")
                value = value.strip()
                if not value:
                    if field.get("required"):
                        raise ValueError(f"{field['name']} is required.")
                    continue
                try:
                    if kind == "number" and not math.isfinite(float(value)):
                        raise ValueError("Enter a finite number.")
                    if kind == "date":
                        date.fromisoformat(value)
                    if kind == "timestamp":
                        datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if kind == "url" and not re.match(r"^https?://[^\s]+$", value):
                        raise ValueError("URL must start with http:// or https://.")
                    if kind == "dropdown" and value not in field.get("options", []):
                        raise ValueError("Choose one of the configured options.")
                except ValueError as error:
                    raise ValueError(f"{field['name']}: {error}") from error
        # Use requests' actual multipart encoder so the check includes every boundary/header.
        maximum = limits.get("max_request_bytes", 128 * 1024**2)
        if (
            maximum
            and sum((self.path / i["file"]).stat().st_size for i in selected) > maximum
        ):
            raise ValueError(
                "Upload exceeds the server's size limit. Resize or exclude photos."
            )
        import requests
        from contextlib import ExitStack

        with ExitStack() as stack:
            files = [
                (
                    "images",
                    (
                        i["file"],
                        stack.enter_context(open(self.path / i["file"], "rb")),
                        "image/jpeg",
                    ),
                )
                for i in selected
            ]
            body = (
                requests.Request(
                    "POST",
                    "http://localhost",
                    data={"metadata": json.dumps(self.submission())},
                    files=files,
                )
                .prepare()
                .body
            )
            maximum = limits.get("max_request_bytes", 128 * 1024**2)
            if maximum and len(body) > maximum:
                raise ValueError(
                    f"Upload exceeds the server's {maximum / 1024**2:g} MiB limit. Resize or exclude photos."
                )

    def prepare(self):
        if self.data["status"] == "conflict":
            raise ValueError("Resolve the upload conflict before submitting again.")
        if self.data["status"] == "draft":
            self.validate()
        self.data["request_id"] = self.data["request_id"] or str(uuid.uuid4())
        self.data["status"] = "sending"
        self.save()
