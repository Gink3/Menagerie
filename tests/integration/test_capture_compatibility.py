"""Exercise desktop-produced multipart uploads against Menagerie's HTTP API."""
import queue
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import app as host
from PIL import Image
from test_api_keys import DatabaseTest
from werkzeug.serving import make_server, WSGIRequestHandler

try:
    from capture_station.camera import Camera
    from mini_scan.client import Client, APIError
    from mini_scan.storage import Store
    DESKTOP_AVAILABLE = True
except ModuleNotFoundError:
    DESKTOP_AVAILABLE = False


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


@unittest.skipUnless(DESKTOP_AVAILABLE, 'Install capture/ to run API compatibility tests')
class CaptureCompatibilityTests(DatabaseTest):
    def setUp(self):
        super().setUp()
        self.config_patch = patch.dict(host.app.config, MINI_SCAN_REQUIRED_FIELDS={})
        self.config_patch.start()
        self.headers = self.key()
        with host.app.app_context():
            db = host.get_db()
            db.execute("insert into collections(name,owner_id) values('Capture',1)")
            db.commit()
        self.server = make_server('127.0.0.1', 0, host.app, request_handler=QuietHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.api = Client(f'http://127.0.0.1:{self.server.server_port}', self.headers['Authorization'].split(' ', 1)[1])
        self.user, collections = self.api.connect()
        self.collection = collections[0]
        self.store = Store(Path(self.temp.name) / 'drafts')
        self.image = Path(self.temp.name) / 'import.png'
        Image.new('RGB', (32, 24), 'red').save(self.image)

    def tearDown(self):
        if hasattr(self, 'server'):
            self.server.shutdown()
            self.thread.join(3)
            self.server.server_close()
        self.config_patch.stop()
        super().tearDown()

    def make_draft(self):
        draft = self.store.create(self.api.url, self.user['id'], self.collection)
        draft.data['name'] = 'Captured miniature'
        return draft

    def test_camera_records_image_order_advanced_schema_and_retry(self):
        draft = self.make_draft()
        events = queue.Queue()
        camera = Camera('Simulator', (320, 240), events)
        photo = Path(self.temp.name) / 'camera.png'
        try:
            camera.capture('integration', photo, metadata={'lighting': dict(enabled=True, brightness=35, frequency=20000, gpio=22, simulated=True)})
            while True:
                event = events.get(timeout=10)
                self.assertNotEqual(event['type'], 'camera_error', event)
                if event['type'] == 'photo_saved':
                    break
        finally:
            camera.close()
            camera.thread.join(3)
        draft.add_image(photo)
        draft.data['images'][-1]['capture'] = event['capture']
        draft.add_image(self.image)
        draft.data['images'].reverse()
        draft.prepare()
        result = self.api.upload(draft)
        item_id = result['item_id']
        records = self.api.request('GET', f'/items/{item_id}/captures')['images']
        self.assertIsNone(records[0]['capture'])
        self.assertEqual(records[1]['capture']['lighting']['brightness'], 35)
        self.assertTrue(records[1]['capture']['simulated_camera'])
        self.assertEqual(records[1]['capture']['focus']['roi'], event['capture']['focus']['roi'])
        fields = self.api.connect()[1][0]['fields']
        capture_fields = [field for field in fields if field.get('capture_key')]
        self.assertEqual(len(capture_fields), 8)
        self.assertTrue(all(field['advanced'] for field in capture_fields))
        replay = self.api.upload(draft)
        self.assertEqual(replay['item_id'], item_id)
        self.assertTrue(replay['replayed'])
        draft.data['name'] = 'Changed frozen request'
        with self.assertRaises(APIError) as rejected:
            self.api.upload(draft)
        self.assertEqual(rejected.exception.status, 409)
        self.assertEqual(len(self.api.request('GET', f'/items/{item_id}/captures')['images']), 2)

    def test_existing_item_append_replace_and_idempotency(self):
        original = self.make_draft()
        original.add_image(self.image)
        original.prepare()
        item_id = self.api.upload(original)['item_id']
        for mode, count in [('append', 2), ('replace', 1)]:
            draft = self.make_draft()
            draft.data.update(target_item_id=item_id, image_mode=mode, item_uuid=original.data['item_uuid'])
            draft.add_image(self.image)
            draft.prepare()
            self.assertEqual(self.api.upload(draft)['item_id'], item_id)
            self.assertTrue(self.api.upload(draft)['replayed'])
            self.assertEqual(len(self.api.request('GET', f'/items/{item_id}/captures')['images']), count)
