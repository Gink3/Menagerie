import json
from werkzeug.datastructures import MultiDict
import app as host
from test_api_keys import DatabaseTest
from scan_api import field_value


class DropdownTests(DatabaseTest):
    def create_collection(self):
        data = MultiDict(
            [
                ("csrf_token", "test-csrf"),
                ("name", "Minis"),
                ("field_name", ""),
                ("field_type", "text"),
                ("field_options", ""),
                ("field_name", "Faction"),
                ("field_type", "dropdown"),
                ("field_options", "Empire\nOrcs\nEmpire\n"),
                ("field_name", "Notes"),
                ("field_type", "text"),
                ("field_options", ""),
            ]
        )
        result = self.client.post("/collections/new", data=data)
        self.assertEqual(result.status_code, 302)
        with host.app.app_context():
            field = (
                host.get_db()
                .execute("select * from fields where name='Faction'")
                .fetchone()
            )
            self.assertEqual(json.loads(field["options_json"]), ["Empire", "Orcs"])
            return field["collection_id"], field["id"]

    def test_create_edit_render_and_add_options_on_collection(self):
        cid, fid = self.create_collection()
        editor = self.client.get(f"/collections/{cid}/edit")
        self.assertEqual(editor.status_code, 200)
        self.assertIn(b'name="field_options"', editor.data)
        self.assertIn(b"Empire\nOrcs", editor.data)
        response = self.client.get(f"/collections/{cid}")
        self.assertNotIn(b"Add option", response.data)
        self.assertIn(f'href="/collections/{cid}/edit?'.encode(), response.data)
        form = self.client.get(f"/collections/{cid}/items/new")
        self.assertIn(f'<select name="field_{fid}">'.encode(), form.data)
        self.assertIn(b'value="Empire"', form.data)
        response = self.client.post(
            f"/collections/{cid}/fields/{fid}/options",
            data={"csrf_token": "test-csrf", "option": "Elves"},
        )
        self.assertEqual(response.status_code, 302)
        self.client.post(
            f"/collections/{cid}/fields/{fid}/options",
            data={"csrf_token": "test-csrf", "option": "Elves"},
        )
        response = self.client.get("/api/v1/collections", headers=self.key())
        dropdown = response.json["collections"][0]["fields"][0]
        self.assertEqual(dropdown["options"], ["Empire", "Orcs", "Elves"])
        self.assertEqual(field_value(dropdown, "Elves"), "Elves")
        with self.assertRaises(ValueError):
            field_value(dropdown, "Not configured")
        result = self.client.post(
            f"/collections/{cid}/edit",
            data=MultiDict(
                [
                    ("csrf_token", "test-csrf"),
                    ("name", "Minis"),
                    ("field_id", str(fid)),
                    ("field_name", "Faction"),
                    ("field_type", "dropdown"),
                    ("field_options", "Empire\nOrcs\nElves\nDwarves"),
                ]
            ),
        )
        self.assertEqual(result.status_code, 302)
        with host.app.app_context():
            field = host.get_field(fid)
            self.assertIn("Dwarves", json.loads(field["options_json"]))

    def test_options_require_manager_and_csrf(self):
        cid, fid = self.create_collection()
        path = f"/collections/{cid}/fields/{fid}/options"
        self.assertEqual(
            self.client.post(path, data={"option": "Other"}).status_code, 400
        )
        self.login(2)
        self.assertEqual(
            self.client.post(
                path, data={"csrf_token": "test-csrf", "option": "Other"}
            ).status_code,
            403,
        )

    def test_invalid_item_value_and_in_use_option_removal_are_rejected(self):
        cid, fid = self.create_collection()
        data = {
            "csrf_token": "test-csrf",
            "name": "Mini",
            f"field_{fid}": "Not an option",
        }
        self.assertEqual(
            self.client.post(f"/collections/{cid}/items/new", data=data).status_code,
            400,
        )
        with host.app.app_context():
            self.assertEqual(
                host.get_db().execute("select count(*) from items").fetchone()[0], 0
            )
        data[f"field_{fid}"] = "Empire"
        self.assertEqual(
            self.client.post(f"/collections/{cid}/items/new", data=data).status_code,
            302,
        )
        result = self.client.post(
            f"/admin/fields/{fid}/update",
            data={
                "csrf_token": "test-csrf",
                "name": "Faction",
                "field_type": "dropdown",
                "field_options": "Orcs",
            },
        )
        self.assertEqual(result.status_code, 400)
        self.assertEqual(
            self.client.get(f"/collections/{cid}?field_{fid}=Empire").status_code, 200
        )

    def test_empty_dropdown_definition_rejects_entire_collection(self):
        result = self.client.post(
            "/collections/new",
            data={
                "csrf_token": "test-csrf",
                "name": "Invalid",
                "field_name": "Faction",
                "field_type": "dropdown",
                "field_options": "",
            },
        )
        self.assertEqual(result.status_code, 400)
        with host.app.app_context():
            self.assertEqual(
                host.get_db().execute("select count(*) from collections").fetchone()[0],
                0,
            )
