import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as host
from api_keys import digest


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [
            patch.object(host, "DATA_DIR", root),
            patch.object(host, "DATABASE", root / "test.sqlite"),
            patch.object(host, "UPLOAD_DIR", root / "uploads"),
            patch.object(host, "WIKI_UPLOAD_DIR", root / "uploads/wiki"),
            patch.dict("os.environ", MENAGERIE_ALLOW_INSECURE_DEFAULTS="1"),
        ]
        for p in self.patches:
            p.start()
        self.client = host.app.test_client()
        with host.app.app_context():
            host.init_db()
            db = host.get_db()
            db.execute(
                "insert into users(username,password_hash,role) values('other','unused','collector')"
            )
            db.commit()
        self.login(1)

    def login(self, user):
        with self.client.session_transaction() as session:
            session["user_id"] = user
            session["csrf_token"] = "test-csrf"

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def key(self, user=1):
        raw = f"mg_test_key_for_{user}"
        with host.app.app_context():
            db = host.get_db()
            db.execute(
                "insert into api_keys(user_id,name,prefix,key_hash) values(?,?,?,?)",
                (user, "Test", raw[:11], digest(raw)),
            )
            db.commit()
        return {"Authorization": "Bearer " + raw}


class KeyTests(DatabaseTest):
    def test_limit_csrf_hashes_and_owner_revocation(self):
        self.assertEqual(
            self.client.post(
                "/settings/api-keys", data={"name": "No CSRF"}
            ).status_code,
            400,
        )
        for i in range(5):
            response = self.client.post(
                "/settings/api-keys",
                data={"name": f"Key {i}", "csrf_token": "test-csrf"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"mg_", response.data)
        self.assertEqual(
            self.client.post(
                "/settings/api-keys", data={"name": "Sixth", "csrf_token": "test-csrf"}
            ).status_code,
            400,
        )
        with host.app.app_context():
            rows = host.get_db().execute("select * from api_keys").fetchall()
            self.assertTrue(all(len(row["key_hash"]) == 64 for row in rows))
        self.login(2)
        self.client.post(
            "/settings/api-keys",
            data={"action": "revoke", "key_id": 1, "csrf_token": "test-csrf"},
        )
        with host.app.app_context():
            self.assertIsNone(
                host.get_db()
                .execute("select revoked_at from api_keys where id=1")
                .fetchone()[0]
            )
        self.login(1)
        self.client.post(
            "/settings/api-keys",
            data={"action": "revoke", "key_id": 1, "csrf_token": "test-csrf"},
        )
        self.assertEqual(
            self.client.post(
                "/settings/api-keys",
                data={"name": "Replacement", "csrf_token": "test-csrf"},
            ).status_code,
            200,
        )
