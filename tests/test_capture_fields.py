import json
import app as host
from test_scan_api import ScanTests


class CaptureFieldsTests(ScanTests):
    def test_records_advanced_visibility_and_preservation(self):
        record = dict(version=1, captured_at='2026-10-09T12:00:00Z', camera='Simulator', width=640, height=480,
                      lighting=dict(enabled=True, brightness=35, gpio=22, frequency=20000),
                      focus=dict(method='laplacian_variance_gaussian3_v1', score=123.5, roi=[.4,.4,.6,.6]))
        self.payload['captures'] = [record]
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.json)
        item = response.json['item_id']
        with host.app.app_context():
            fields = host.get_db().execute('select * from fields where capture_key is not null').fetchall()
            self.assertEqual(len(fields), 8)
            self.assertTrue(all(f['advanced'] for f in fields))
        hidden = self.client.get(f'/items/{item}')
        self.assertNotIn(b'Capture: Light Brightness', hidden.data)
        visible = self.client.get(f'/items/{item}?advanced=1')
        self.assertIn(b'Capture: Light Brightness', visible.data)
        self.assertIn(b'35', visible.data)
        self.client.get(f'/items/{item}?advanced=0')
        edited = self.client.post(f'/items/{item}/edit', data=dict(csrf_token='test-csrf',name='Edited',item_uuid=self.payload['item_uuid']))
        self.assertEqual(edited.status_code, 302)
        with host.app.app_context():
            self.assertEqual(host.get_db().execute("select value from item_values join fields on fields.id=field_id where capture_key='light_brightness'").fetchone()[0], '35')
        records = self.client.get(f'/api/v1/items/{item}/captures', headers=self.headers)
        self.assertEqual(records.status_code, 200)
        self.assertIn('123.5', json.dumps(records.json))
        self.assertEqual(self.upload().status_code, 200)

    def test_invalid_record_is_atomic(self):
        self.payload['captures'] = [dict(version=1)]
        self.assertEqual(self.upload().status_code, 422)
        with host.app.app_context():
            self.assertEqual(host.get_db().execute('select count(*) from items').fetchone()[0], 0)
            self.assertEqual(host.get_db().execute('select count(*) from fields where advanced=1').fetchone()[0], 0)

    def test_schema_ensure_is_idempotent_and_conflicts_are_atomic(self):
        url = '/api/v1/collections/1/capture-fields'
        self.assertEqual(self.client.post(url, headers=self.headers).status_code, 200)
        self.assertEqual(self.client.post(url, headers=self.headers).status_code, 200)
        with host.app.app_context():
            db = host.get_db()
            self.assertEqual(db.execute('select count(*) from fields where capture_key is not null').fetchone()[0], 8)
            db.execute("update fields set field_type='text' where capture_key='light_brightness'")
            db.commit()
        self.assertEqual(self.client.post(url, headers=self.headers).status_code, 422)
        self.assertEqual(self.client.post('/api/v1/collections/2/capture-fields', headers=self.headers).status_code, 404)
