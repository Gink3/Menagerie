"""Bounded HTTP requests; credentials live in the operating system keyring."""

import json
from contextlib import ExitStack
from urllib.parse import urlsplit
import requests
import keyring


class APIError(Exception):
    def __init__(self, message, status=0, details=None):
        self.status = status
        self.details = details or {}
        issues = self.details.get("issues", [])
        super().__init__(
            message
            + (
                "\n\n"
                + "\n".join(issues)
                + "\n\nEdit the collection, close the draft and refresh collections."
                if issues
                else ""
            )
        )


def server_url(value):
    value = value.strip().rstrip("/")
    parts = urlsplit(value)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            "Enter a Menagerie HTTP(S) URL without credentials, query or fragment."
        )
    if parts.scheme == "http" and parts.hostname not in (
        "localhost",
        "127.0.0.1",
        "::1",
    ):
        raise ValueError(
            "Use HTTPS for a remote Menagerie server to protect your API key."
        )
    return value


class Client:
    def __init__(self, url, key):
        self.url = server_url(url)
        self.key = key.strip()
        if not self.key:
            raise ValueError("Enter your API key.")

    def request(self, method, path, **kwargs):
        headers = {"Authorization": "Bearer " + self.key, **kwargs.pop("headers", {})}
        try:
            response = requests.request(
                method,
                self.url + "/api/v1" + path,
                headers=headers,
                timeout=(10, 90),
                allow_redirects=False,
                **kwargs,
            )
        except requests.RequestException as error:
            raise APIError(
                "Cannot reach Menagerie. Your local draft is retained; retry when connected."
            ) from error
        try:
            data = response.json()
        except ValueError:
            raise APIError(
                "Menagerie returned an unexpected response. Check the URL and server version.",
                response.status_code,
            )
        if not response.ok or 300 <= response.status_code < 400:
            raise APIError(
                data.get("error", "Menagerie rejected the request."),
                response.status_code,
                data,
            )
        return data

    def connect(self):
        return self.request("GET", "/me"), self.request("GET", "/collections")[
            "collections"
        ]

    def lookup(self, uid):
        try:
            return self.request("GET", "/items/by-rfid/" + uid)
        except APIError as error:
            if error.status == 404:
                return None
            raise

    def upload(self, draft):
        if draft.data.get("lookup_pending") and not draft.data.get("target_item_id"):
            found = self.lookup(draft.data["rfid_uid"])
            if found and found["item"]["item_uuid"] != draft.data["item_uuid"]:
                raise APIError(
                    "This tag already belongs to an item. Choose Add photos to existing item.",
                    409,
                    {"code": "identity_conflict", **found},
                )
        with ExitStack() as stack:
            files = [
                (
                    "images",
                    (
                        image["file"],
                        stack.enter_context(open(draft.path / image["file"], "rb")),
                        "image/jpeg",
                    ),
                )
                for image in draft.data["images"]
                if image["selected"]
            ]
            return self.request(
                "POST",
                f"/items/{draft.data['target_item_id']}/images"
                if draft.data.get("target_item_id")
                else f"/collections/{draft.data['collection_id']}/items",
                headers={"Idempotency-Key": draft.data["request_id"]},
                data={"metadata": json.dumps(draft.submission())},
                files=files,
            )


def remember(url, key):
    keyring.set_password("menagerie-mini-scan", server_url(url), key)


def recalled(url):
    return keyring.get_password("menagerie-mini-scan", server_url(url)) or ""
