import io
import json
import uuid
from PIL import Image
import app as host
from test_api_keys import DatabaseTest


class ScanTests(DatabaseTest):
    def setUp(self):
        super().setUp()
        self.headers = self.key()
        with host.app.app_context():
            db = host.get_db()
            db.execute("insert into collections(name,owner_id) values('Minis',1)")
            db.execute("insert into collections(name,owner_id) values('Private',2)")
            db.execute(
                "insert into fields(collection_id,name,field_type) values(1,'Painted','checkbox')"
            )
            db.commit()
        self.payload = dict(
            item_uuid=str(uuid.uuid4()),
            rfid_uid="DEADBEEF",
            name="Captain",
            description="",
            tags="army",
            values={"1": "true"},
        )
        self.request_id = str(uuid.uuid4())

    def upload(self, collection=1, payload=None, raw=None):
        image = io.BytesIO()
        Image.new("RGB", (32, 24), "purple").save(image, format="PNG")
        return self.client.post(
            f"/api/v1/collections/{collection}/items",
            headers={**self.headers, "Idempotency-Key": self.request_id},
            data={
                "metadata": json.dumps(payload or self.payload),
                "images": (io.BytesIO(raw or image.getvalue()), "mini.png"),
            },
        )

    def test_owner_scope_bearer_only_and_revocation(self):
        self.assertEqual(self.client.get("/api/v1/collections").status_code, 401)
        response = self.client.get("/api/v1/collections", headers=self.headers)
        self.assertEqual([c["name"] for c in response.json["collections"]], ["Minis"])
        self.assertEqual(self.upload(2).status_code, 404)
        with host.app.app_context():
            db = host.get_db()
            db.execute("update api_keys set revoked_at=current_timestamp")
            db.commit()
        self.assertEqual(
            self.client.get("/api/v1/me", headers=self.headers).status_code, 401
        )

    def test_replay_order_and_conflicting_tag(self):
        result = self.upload()
        self.assertEqual(result.status_code, 201, result.json)
        again = self.upload()
        self.assertEqual(again.status_code, 200, again.json)
        self.assertTrue(again.json["replayed"])
        with host.app.app_context():
            db = host.get_db()
            self.assertEqual(db.execute("select count(*) from items").fetchone()[0], 1)
            self.assertEqual(
                db.execute("select position from item_images").fetchone()[0], 0
            )
        self.request_id = str(uuid.uuid4())
        self.assertEqual(self.upload().status_code, 409)
        self.assertEqual(
            self.client.get(
                "/api/v1/items/by-rfid/DEADBEEF", headers=self.headers
            ).status_code,
            200,
        )

    def test_invalid_images_values_and_required_schema(self):
        self.assertEqual(self.upload(raw=b"not an image").status_code, 422)
        bad = {**self.payload, "values": {"999": "foreign"}}
        self.assertEqual(self.upload(payload=bad).status_code, 422)
        with host.app.app_context():
            self.assertEqual(
                host.get_db().execute("select count(*) from items").fetchone()[0], 0
            )
        host.app.config["MINI_SCAN_REQUIRED_FIELDS"] = {
            "Manufacturer": "text",
            "Painted": "text",
        }
        try:
            result = self.upload()
            self.assertEqual(result.status_code, 422)
            self.assertEqual(len(result.json["issues"]), 2)
        finally:
            host.app.config.pop("MINI_SCAN_REQUIRED_FIELDS")

    def test_fingerprint_rejects_changed_retry(self):
        self.assertEqual(self.upload().status_code, 201)
        self.assertEqual(
            self.upload(payload={**self.payload, "name": "Changed"}).status_code, 409
        )

    def test_web_uuid_creation_and_later_tag_assignment(self):
        created = self.client.post(
            "/collections/1/items/new",
            data={"csrf_token": "test-csrf", "name": "Untagged"},
        )
        self.assertEqual(created.status_code, 302)
        with host.app.app_context():
            item = host.get_db().execute("select * from items").fetchone()
            ident = item["item_uuid"]
            item_id = item["id"]
            self.assertEqual(str(uuid.UUID(ident)), ident)
            self.assertIsNone(item["rfid_uid"])
        updated = self.client.post(
            f"/items/{item_id}/edit",
            data={
                "csrf_token": "test-csrf",
                "name": "Tagged",
                "item_uuid": ident,
                "rfid_uid": "aa:bb:cc:dd",
            },
        )
        self.assertEqual(updated.status_code, 302)
        with host.app.app_context():
            item = host.get_db().execute("select * from items").fetchone()
            self.assertEqual(item["item_uuid"], ident)
            self.assertEqual(item["rfid_uid"], "AABBCCDD")
        response = self.client.post(
            f"/items/{item_id}/edit",
            data={"csrf_token": "test-csrf", "name": "Bad", "item_uuid": "invalid"},
        )
        self.assertEqual(response.status_code, 302)
        with host.app.app_context():
            self.assertEqual(
                host.get_db().execute("select name from items").fetchone()[0], "Tagged"
            )

    def test_replay_survives_collection_requirement_change(self):
        created = self.upload()
        self.assertEqual(created.status_code, 201)
        host.app.config["MINI_SCAN_REQUIRED_FIELDS"] = {"New field": "text"}
        try:
            repeated = self.upload()
            self.assertEqual(repeated.status_code, 200, repeated.json)
            self.assertEqual(repeated.json["item_id"], created.json["item_id"])
        finally:
            host.app.config.pop("MINI_SCAN_REQUIRED_FIELDS")
