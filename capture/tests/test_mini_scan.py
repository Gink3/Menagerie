import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PIL import Image
from mini_scan.storage import Store, Draft
from mini_scan.capture import Sequence
from mini_scan.client import server_url, Client, APIError

SCHEMA = {
    "id": 1,
    "name": "Minis",
    "fields": [{"id": 7, "name": "Painted", "field_type": "checkbox"}],
    "issues": [],
}


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.draft = self.store.create("http://localhost", 1, SCHEMA, "DEADBEEF")
        self.source = Path(self.temp.name) / "source.png"
        Image.new("RGB", (100, 80), "purple").save(self.source)

    def test_custom_mini_id_is_not_automatically_populated(self):
        schema = {
            **SCHEMA,
            "fields": [{"id": 8, "name": "Mini ID", "field_type": "text"}],
        }
        draft = self.store.create("http://localhost", 1, schema, "12345678")
        self.assertEqual(draft.data["values"], {})

    def test_manual_drafts_have_distinct_persistent_uuids(self):
        first = self.store.create("http://localhost", 1, SCHEMA)
        second = self.store.create("http://localhost", 1, SCHEMA)
        self.assertNotEqual(first.key, second.key)
        self.assertIsNone(first.data["rfid_uid"])
        self.assertEqual(Draft(first.path).key, first.key)

    def test_account_isolation_and_tag_dedup(self):
        self.assertEqual(
            self.store.create("http://localhost", 1, SCHEMA, "DEADBEEF").path,
            self.draft.path,
        )
        self.assertIsNone(self.store.for_tag("http://localhost", 2, "DEADBEEF"))
        self.assertIsNone(self.store.for_tag("https://elsewhere", 1, "DEADBEEF"))

    def test_final_edits_order_selection_and_freeze(self):
        self.draft.add_image(self.source)
        self.draft.add_image(self.source)
        self.draft.edit(0, (10, 10, 90, 70), (40, 30))
        with Image.open(
            self.draft.path / self.draft.data["images"][0]["file"]
        ) as image:
            self.assertEqual(image.size, (40, 30))
        self.draft.data["images"].reverse()
        self.draft.data["images"][0]["selected"] = False
        self.draft.data["name"] = "Mini"
        self.draft.prepare()
        request = self.draft.data["request_id"]
        with self.assertRaises(ValueError):
            self.draft.delete_image(0)
        self.draft.data["status"] = "uncertain"
        self.draft.save()
        restored = Draft(self.draft.path)
        restored.prepare()
        self.assertEqual(restored.data["request_id"], request)
        self.assertFalse(restored.data["images"][0]["selected"])
        restored.data["status"] = "uploaded"
        restored.prepare()
        self.assertEqual(restored.data["request_id"], request)

    def test_conflict_recovery_preserves_photos_and_changes_identity(self):
        self.draft.add_image(self.source)
        self.draft.data["name"] = "Mini"
        self.draft.prepare()
        old = self.draft.data["item_uuid"]
        images = list(self.draft.data["images"])
        self.draft.data.update(
            status="conflict", conflict={"code": "identity_conflict"}
        )
        with self.assertRaises(ValueError):
            self.draft.prepare()
        with self.assertRaises(ValueError):
            self.draft.delete_image(0)
        self.draft.recover_conflict()
        self.assertNotEqual(old, self.draft.data["item_uuid"])
        self.assertIsNone(self.draft.data["rfid_uid"])
        self.assertIsNone(self.draft.data["request_id"])
        self.assertEqual(images, self.draft.data["images"])
        self.assertEqual(self.draft.data["name"], "Mini")
        self.assertEqual(Draft(self.draft.path).data["status"], "draft")
        self.draft.prepare()

    def test_recapture_keeps_identity_and_reuses_pending_session(self):
        item = dict(
            id=42,
            item_uuid=self.draft.data["item_uuid"],
            rfid_uid="DEADBEEF",
            name="Mini",
        )
        first = self.store.recapture("http://localhost", 1, SCHEMA, item)
        second = self.store.recapture("http://localhost", 1, SCHEMA, item)
        self.assertEqual(first.path, second.path)
        self.assertEqual(first.submission()["image_mode"], "append")
        self.assertEqual(first.data["item_uuid"], item["item_uuid"])
        first.data["status"] = "uploaded"
        first.save()
        third = self.store.recapture("http://localhost", 1, SCHEMA, item)
        self.assertNotEqual(first.path, third.path)

    def test_preflight_rejects_limits_and_invalid_fields_before_freezing(self):
        self.draft.add_image(self.source)
        self.draft.data["name"] = "Mini"
        self.draft.data["schema"]["upload_limits"] = {"max_request_bytes": 1}
        with self.assertRaisesRegex(ValueError, "Upload exceeds"):
            self.draft.prepare()
        self.assertEqual(self.draft.data["status"], "draft")
        self.assertIsNone(self.draft.data["request_id"])
        self.draft.data["schema"]["upload_limits"] = {"max_images": 1}
        self.draft.add_image(self.source)
        with self.assertRaisesRegex(ValueError, "between 1 and 1"):
            self.draft.prepare()
        self.draft.data["images"][1]["selected"] = False
        self.draft.data["schema"]["fields"] = [
            {"id": 8, "name": "Cost", "field_type": "number", "required": True}
        ]
        with self.assertRaisesRegex(ValueError, "Cost is required"):
            self.draft.prepare()
        self.draft.data["values"] = {"8": "nan"}
        with self.assertRaisesRegex(ValueError, "finite"):
            self.draft.prepare()
        self.draft.data["values"] = {"8": "5"}
        self.draft.prepare()

    def test_original_survives_edits_and_can_be_restored(self):
        self.draft.add_image(self.source)
        original = self.draft.path / self.draft.data["images"][0]["original"]
        self.assertEqual(original.read_bytes(), self.source.read_bytes())
        self.draft.edit(0, size=(10, 8))
        self.assertEqual(original.read_bytes(), self.source.read_bytes())
        self.draft.restore_original(0)
        with Image.open(
            self.draft.path / self.draft.data["images"][0]["file"]
        ) as image:
            self.assertEqual(image.size, (100, 80))

    def test_offline_cache_is_scoped_and_contains_no_key(self):
        self.store.cache_account(
            "http://localhost", {"id": 1, "username": "Test"}, [SCHEMA]
        )
        accounts = self.store.cached_accounts("http://localhost")
        self.assertEqual(accounts[0]["user"]["id"], 1)
        self.assertEqual(self.store.cached_accounts("https://other"), [])
        self.assertNotIn("key", accounts[0])

    def test_deferred_lookup_handles_existing_tag_and_lost_response(self):
        self.draft.add_image(self.source)
        self.draft.data.update(name="Mini", lookup_pending=True)
        self.draft.prepare()
        client = Client("http://localhost", "key")
        other = {"item": {"item_uuid": "different", "id": 42}, "url": "/items/42/edit"}
        with (
            patch.object(client, "lookup", return_value=other),
            patch.object(client, "request") as request,
        ):
            with self.assertRaises(APIError) as error:
                client.upload(self.draft)
            self.assertEqual(error.exception.status, 409)
            request.assert_not_called()
        own = {"item": {"item_uuid": self.draft.data["item_uuid"]}}
        with (
            patch.object(client, "lookup", return_value=own),
            patch.object(client, "request", return_value={"item_id": 42}) as request,
        ):
            self.assertEqual(client.upload(self.draft)["item_id"], 42)
            self.assertEqual(request.call_args.args[1], "/collections/1/items")
        self.draft.data["target_item_id"] = 42
        with (
            patch.object(client, "lookup") as lookup,
            patch.object(client, "request") as request,
        ):
            client.upload(self.draft)
            lookup.assert_not_called()
            self.assertEqual(request.call_args.args[1], "/items/42/images")

    def test_cleanup_preserves_unsent_and_protected(self):
        self.draft.add_image(self.source)
        sent = self.store.create("http://localhost", 1, SCHEMA, "12345678")
        sent.add_image(self.source)
        sent.data["status"] = "uploaded"
        sent.save()
        self.store.prune_uploaded(0, [sent.path])
        self.assertTrue(sent.path.exists())
        self.store.prune_uploaded(0)
        self.assertFalse(sent.path.exists())
        self.assertTrue(self.draft.path.exists())

    def test_remote_plaintext_credentials_rejected(self):
        with self.assertRaises(ValueError):
            server_url("http://example.com")
        self.assertEqual(server_url("http://localhost:8000/"), "http://localhost:8000")


class SequenceTests(unittest.TestCase):
    def make_sequence(self):
        self.commands = []
        self.now = 0
        self.camera = Mock()
        self.draft = Mock()
        self.draft.data = {"images": []}
        self.draft.add_image.side_effect = lambda path: self.draft.data[
            "images"
        ].append({})
        self.done = Mock()

        def send(command, **kwargs):
            self.commands.append((command, kwargs))
            return len(self.commands)

        self.seq = Sequence(
            send,
            self.camera,
            self.draft,
            Mock(),
            self.done,
            settle=1,
            clock=lambda: self.now,
        )
        self.seq.start({"busy": False, "steps_per_rev": 3200})

    def acknowledge(self):
        self.seq.event({"type": "done", "id": self.seq.wait_id})

    def test_six_photos_with_moves_after_each_and_final_return(self):
        self.make_sequence()
        self.assertEqual(self.commands[0][0], "HOME")
        self.draft.path = Path("/tmp")
        for i in range(6):
            self.acknowledge()
            self.now += 2
            self.seq.tick()
            self.assertEqual(self.camera.capture.call_count, i + 1)
            self.seq.event(
                {
                    "type": "photo_saved",
                    "token": self.seq.token,
                    "path": "/tmp/mini-scan-test-nonexistent.png",
                }
            )
            self.assertEqual(self.commands[-1], ("ROTATE", {"degrees": 60}))
        self.assertTrue(self.seq.active)
        self.acknowledge()
        self.assertEqual(self.commands[-1][0], "RETURN")
        self.acknowledge()
        self.assertFalse(self.seq.active)
        self.assertTrue(self.done.call_args.args[0])

    def test_targeted_retake_homes_moves_captures_and_returns(self):
        self.make_sequence()
        self.seq.angle = 120
        self.draft.path = Path("/tmp")
        self.acknowledge()
        self.assertEqual(self.commands[-1], ("ROTATE", {"degrees": 60}))
        self.acknowledge()
        self.assertEqual(self.commands[-1], ("ROTATE", {"degrees": 60}))
        self.acknowledge()
        self.now += 2
        self.seq.tick()
        self.seq.event(
            {
                "type": "photo_saved",
                "token": self.seq.token,
                "path": "/tmp/no-such-retake.png",
            }
        )
        self.assertEqual(self.commands[-1][0], "RETURN")
        self.assertEqual(self.draft.data["images"][-1]["angle"], 120)
        self.acknowledge()
        self.assertFalse(self.seq.active)

    def test_current_view_requires_no_motor_or_motion(self):
        self.make_sequence()
        self.commands.clear()
        self.seq.active = False
        self.seq.angle = "current"
        self.draft.path = Path("/tmp")
        self.seq.start({})
        self.now += 2
        self.seq.tick()
        self.seq.event(
            {
                "type": "photo_saved",
                "token": self.seq.token,
                "path": "/tmp/no-such-current.png",
            }
        )
        self.assertFalse(self.seq.active)
        self.assertEqual(self.commands, [])
        self.assertTrue(self.done.call_args.args[0])

    def test_disconnect_stops_and_late_events_do_not_resume(self):
        self.make_sequence()
        self.seq.event({"type": "disconnected"})
        self.assertEqual(self.commands[-1][0], "STOP")
        count = len(self.commands)
        self.seq.event({"type": "done", "id": 1})
        self.now += 200
        self.seq.tick()
        self.assertEqual(len(self.commands), count)
        self.camera.capture.assert_not_called()


@unittest.skipUnless(os.environ.get("DISPLAY"), "Needs desktop display")
class UITests(unittest.TestCase):
    def setUp(self):
        from tkinterdnd2 import TkinterDnD
        from mini_scan.app import App

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = TkinterDnD.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        with patch("mini_scan.app.recalled", return_value=""):
            self.app = App(self.root, Store(self.temp.name))
        self.app.client = Mock(url="http://localhost")
        self.app.user = {"id": 1, "username": "Test"}
        self.app.collections = [SCHEMA]
        self.app.collection["values"] = ["Minis"]
        self.app.collection.current(0)
        self.addCleanup(self.app.close)

    def test_same_tag_gets_one_dialog_and_persistent_draft(self):
        self.app.async_call = lambda work, done: done(None, None)
        for _ in range(5):
            self.app.scan("DEADBEEF")
        self.assertEqual(len(self.app.dialogs), 1)
        dialog = self.app.dialogs["DEADBEEF"]
        dialog.variables["name"].set("Mini")
        dialog.save()
        self.root.update()
        self.assertFalse(self.errors, self.errors)
        dialog.close()
        draft = self.app.store.for_tag("http://localhost", 1, "DEADBEEF")
        self.assertEqual(draft.data["name"], "Mini")
        self.app.open_draft(draft)
        self.assertEqual(len(self.app.dialogs), 1)
        self.app.dialogs["DEADBEEF"].close()

    def test_simulated_capture_returns_home_and_keeps_six_photos(self):
        import time
        from capture_station.camera import Camera

        self.app.port.set("Simulator")
        self.app.steps.set("60")
        self.app.speed.set("1000")
        self.app.connect_device()
        self.app.camera = Camera("Simulator", (160, 120), self.app.events)

        def wait_for(condition, seconds=20):
            end = time.monotonic() + seconds
            while time.monotonic() < end and not condition():
                self.root.update()
                time.sleep(0.01)
            self.assertTrue(condition(), self.errors)

        wait_for(lambda: self.app.configured and self.app.camera_ready)
        draft = self.app.store.create("http://localhost", 1, SCHEMA, "DEADBEEF")
        self.app.open_draft(draft)
        dialog = self.app.dialogs["DEADBEEF"]
        dialog.capture()
        wait_for(lambda: not dialog.capture_active)
        self.assertEqual(len(draft.data["images"]), 6)
        self.assertEqual(self.app.state["angle"], 0)
        self.assertFalse(self.app.state["busy"])
        self.assertFalse(self.errors, self.errors)

    def test_conflict_ui_recovers_without_losing_photos(self):
        draft = self.app.store.create("http://localhost", 1, SCHEMA, "DEADBEEF")
        source = Path(self.temp.name) / "photo.png"
        Image.new("RGB", (32, 24), "purple").save(source)
        draft.add_image(source)
        draft.data["name"] = "Mini"
        draft.save()
        self.app.open_draft(draft)
        dialog = self.app.dialogs[draft.key]
        self.app.async_call = lambda work, done: done(
            None, APIError("Duplicate", 409, {"code": "identity_conflict"})
        )
        with patch.object(self.app, "fail"):
            dialog.upload()
        dialog = self.app.dialogs[draft.key]
        self.assertEqual(dialog.draft.data["status"], "conflict")
        self.assertTrue(dialog.upload_button.instate(["disabled"]))
        with patch("mini_scan.app.messagebox.askyesno", return_value=True):
            dialog.recover_conflict()
        reopened = next(iter(self.app.dialogs.values()))
        self.assertEqual(reopened.draft.data["status"], "draft")
        self.assertIsNone(reopened.draft.data["rfid_uid"])
        self.assertEqual(len(reopened.draft.data["images"]), 1)

    def test_offline_intake_uses_cached_schema_without_network(self):
        self.app.store.cache_account("http://localhost", self.app.user, [SCHEMA])
        self.app.url.set("http://localhost")
        self.app.use_offline()
        chooser = next(
            w for w in self.root.winfo_children() if w.winfo_class() == "Toplevel"
        )
        from tkinter import ttk

        next(w for w in chooser.winfo_children() if isinstance(w, ttk.Button)).invoke()
        self.assertTrue(self.app.offline)
        self.app.async_call = Mock()
        self.app.scan("DEADBEEF")
        self.app.async_call.assert_not_called()
        self.assertTrue(self.app.dialogs["DEADBEEF"].draft.data["lookup_pending"])
        self.app.manual_item()
        self.assertEqual(len(self.app.dialogs), 2)

    def test_current_view_capture_with_camera_and_no_pico(self):
        import time
        from capture_station.camera import Camera

        self.app.camera = Camera("Simulator", (160, 120), self.app.events)
        end = time.monotonic() + 10
        while not self.app.camera_ready and time.monotonic() < end:
            self.root.update()
            time.sleep(0.01)
        self.assertTrue(self.app.camera_ready)
        self.app.manual_item()
        dialog = next(iter(self.app.dialogs.values()))
        dialog.capture("current")
        while dialog.capture_active and time.monotonic() < end:
            self.root.update()
            time.sleep(0.01)
        self.assertFalse(dialog.capture_active)
        self.assertEqual(len(dialog.draft.data["images"]), 1)
        self.assertTrue(
            (dialog.draft.path / dialog.draft.data["images"][0]["original"]).exists()
        )
        self.assertFalse(self.app.connected)
        self.assertFalse(self.errors, self.errors)

    def test_photo_editor_adjustments_preserve_original_and_capture_record(self):
        from mini_scan.app import ImageEditor
        self.app.manual_item()
        dialog = next(iter(self.app.dialogs.values()))
        source = Path(self.temp.name) / "editor.png"
        Image.new("RGB", (100, 80), (40, 40, 40)).save(source)
        dialog.draft.add_image(source)
        entry = dialog.draft.data["images"][0]
        entry["capture"] = {"version": 1, "camera": "test"}
        original = (dialog.draft.path / entry["original"]).read_bytes()
        editor = ImageEditor(dialog, 0)
        editor.adjustments["exposure"].set(1)
        editor.render()
        self.root.update()
        editor.compare.set(True)
        editor.render()
        editor.apply()
        self.root.update()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual((dialog.draft.path / entry["original"]).read_bytes(), original)
        with Image.open(dialog.draft.path / entry["file"]) as edited:
            self.assertEqual(edited.getpixel((0, 0)), (80, 80, 80))
        self.assertEqual(entry["capture"]["camera"], "test")
        dialog.draft.restore_original(0)
        self.assertEqual(entry["edits"], [])

    def test_lighting_toggle_slider_and_disconnect(self):
        import time

        self.app.port.set("Simulator")
        self.app.steps.set("0")
        self.app.speed.set("200")
        self.app.connect_device()
        light = self.app.lighting

        def wait(predicate):
            end = time.monotonic() + 5
            while time.monotonic() < end and not predicate():
                self.root.update()
                time.sleep(0.01)
            self.assertTrue(predicate(), self.errors)

        wait(lambda: light.initialized and light.pending is None)
        light.brightness.set(55)
        light.changed("55")
        wait(lambda: light.pending is None and light.reported["brightness"] == 55)
        self.assertFalse(light.reported["enabled"])
        light.toggle.invoke()
        wait(lambda: light.pending is None and light.reported["enabled"])
        self.assertEqual(light.reported["duty_u16"], round(55 * 65535 / 100))
        self.app.disconnect_device()
        wait(lambda: not self.app.connected)
        self.assertFalse(light.enabled.get())
        self.assertTrue(light.toggle.instate(["disabled"]))
        self.assertFalse(self.errors, self.errors)

    def test_multiple_manual_dialogs_have_distinct_uuids(self):
        self.app.manual_item()
        self.app.manual_item()
        self.assertEqual(len(self.app.dialogs), 2)
        for key, dialog in self.app.dialogs.items():
            self.assertEqual(dialog.uuid_value.get(), key)
            self.assertIsNone(dialog.draft.data["rfid_uid"])
        self.root.update()
        self.assertFalse(self.errors, self.errors)

    def test_dropdown_renders_only_configured_options_and_saves_value(self):
        from tkinter import ttk

        schema = {
            **SCHEMA,
            "fields": [
                {
                    "id": 10,
                    "name": "Faction",
                    "field_type": "dropdown",
                    "options": ["Empire", "Orcs"],
                }
            ],
        }
        self.app.collections = [schema]
        self.app.manual_item()
        dialog = next(iter(self.app.dialogs.values()))

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        dropdown = next(
            w for w in descendants(dialog.window) if isinstance(w, ttk.Combobox)
        )
        self.assertEqual(tuple(dropdown["values"]), ("", "Empire", "Orcs"))
        self.assertEqual(str(dropdown["state"]), "readonly")
        dropdown.set("Orcs")
        dialog.save()
        self.assertEqual(dialog.draft.data["values"]["10"], "Orcs")
