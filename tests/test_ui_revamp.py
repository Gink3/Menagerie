"""Check data visibility and form submission across the refreshed UI."""
from html.parser import HTMLParser

import app as host
from test_api_keys import DatabaseTest


class FormControls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.controls = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('name'):
            self.controls[attrs['name']] = attrs.get('value', '')


class UIRevampTests(DatabaseTest):
    def setUp(self):
        super().setUp()
        with host.app.app_context():
            db = host.get_db()
            db.execute("insert into collections(name,owner_id) values('My miniatures',1)")
            db.execute("insert into collections(name,owner_id) values('Other collection',2)")
            db.execute("insert into items(collection_id,name,item_uuid,rfid_uid) values(1,'Sentinel','770e8400-e29b-41d4-a716-446655440000','AABBCCDD')")
            db.execute("insert into items(collection_id,name) values(2,'Other item')")
            db.execute("insert into item_images(item_id,filename,original_name,gallery_enabled,position) values(1,'private.png','Private view',0,0)")
            db.execute("insert into item_images(item_id,filename,original_name,gallery_enabled,position) values(1,'public.png','Public view',1,1)")
            db.execute("insert into item_images(item_id,filename,original_name) values(2,'other.png','Other view')")
            db.commit()

    def test_collection_covers_only_include_owned_collections(self):
        response = self.client.get('/collections')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'private.png', response.data)
        self.assertIn(b'1 item', response.data)
        self.assertNotIn(b'Other collection', response.data)
        self.assertNotIn(b'other.png', response.data)
        self.login(2)
        response = self.client.get('/collections')
        self.assertIn(b'Other collection', response.data)
        self.assertNotIn(b'private.png', response.data)

    def test_public_photo_viewer_never_exposes_private_images_or_controls(self):
        self.client.post('/logout', data={'csrf_token': 'test-csrf'})
        response = self.client.get('/items/1')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'public.png', response.data)
        self.assertNotIn(b'private.png', response.data)
        self.assertNotIn(b'Manage images', response.data)
        self.assertNotIn(b'More actions', response.data)
        self.assertNotIn(b'Administration', response.data)

    def test_editing_overview_preserves_collapsed_identification_values(self):
        response = self.client.get('/items/1/edit')
        self.assertEqual(response.status_code, 200)
        form = FormControls()
        form.feed(response.get_data(as_text=True))
        data = {key: form.controls[key] for key in ('csrf_token', 'name', 'item_uuid', 'rfid_uid', 'tags')}
        data['name'] = 'Updated Sentinel'
        result = self.client.post('/items/1/edit', data=data)
        self.assertEqual(result.status_code, 302)
        with host.app.app_context():
            item = host.get_db().execute('select * from items where id=1').fetchone()
            self.assertEqual(item['name'], 'Updated Sentinel')
            self.assertEqual(item['item_uuid'], '770e8400-e29b-41d4-a716-446655440000')
            self.assertEqual(item['rfid_uid'], 'AABBCCDD')

    def test_collectors_do_not_receive_admin_navigation(self):
        self.login(2)
        response = self.client.get('/collections')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'Administration', response.data)
        self.assertNotIn(b'>Metrics<', response.data)
        self.assertIn(b'My collections', response.data)
