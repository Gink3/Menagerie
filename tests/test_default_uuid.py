import uuid

import app as host
from test_api_keys import DatabaseTest


class DefaultUUIDTests(DatabaseTest):
    def test_existing_and_new_collections_expose_default_uuid(self):
        with host.app.app_context():
            db = host.get_db()
            for name in ("Existing", "New"):
                cid = db.execute(
                    "insert into collections(name,owner_id) values(?,1)", (name,)
                ).lastrowid
                db.execute(
                    "insert into items(collection_id,name) values(?,?)",
                    (cid, name + " mini"),
                )
            db.commit()
        for cid in (1, 2):
            response = self.client.get(f"/collections/{cid}")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b">UUID</th>", response.data)
            with host.app.app_context():
                item = (
                    host.get_db()
                    .execute("select * from items where collection_id=?", (cid,))
                    .fetchone()
                )
                ident = item["item_uuid"]
                self.assertEqual(str(uuid.UUID(ident)), ident)
                self.assertEqual(
                    host.get_db()
                    .execute(
                        "select count(*) from fields where collection_id=?", (cid,)
                    )
                    .fetchone()[0],
                    0,
                )
            self.assertIn(ident.encode(), response.data)
            self.assertIn(ident.encode(), self.client.get(f"/items/{item['id']}").data)
            self.assertIn(
                ident.encode(),
                self.client.get(f"/collections/{cid}?q={ident}&sort=item_uuid").data,
            )
        schema = self.client.get("/api/v1/collections", headers=self.key()).json
        for collection in schema["collections"]:
            field = next(
                f for f in collection["core_fields"] if f["name"] == "item_uuid"
            )
            self.assertTrue(field["required"])
            self.assertTrue(field["auto_generated"])

    def test_duplicate_receives_new_uuid_immediately(self):
        original = str(uuid.uuid4())
        with host.app.app_context():
            db = host.get_db()
            db.execute("insert into collections(name,owner_id) values('Minis',1)")
            db.execute(
                "insert into items(collection_id,name,item_uuid) values(1,'Mini',?)",
                (original,),
            )
            db.commit()
        result = self.client.post(
            "/items/1/duplicate", data={"csrf_token": "test-csrf"}
        )
        self.assertEqual(result.status_code, 302)
        with host.app.app_context():
            copy = (
                host.get_db()
                .execute("select item_uuid from items where id=2")
                .fetchone()[0]
            )
            self.assertEqual(str(uuid.UUID(copy)), copy)
            self.assertNotEqual(copy, original)
