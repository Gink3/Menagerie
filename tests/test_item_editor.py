import app as host
from test_api_keys import DatabaseTest


class ItemEditorTests(DatabaseTest):
    def setUp(self):
        super().setUp()
        with host.app.app_context():
            db = host.get_db()
            db.execute("insert into collections(name,owner_id) values('Minis',1)")
            db.execute("insert into items(collection_id,name) values(1,'Sentinel')")
            db.execute("insert into item_images(item_id,filename,original_name,gallery_ratio_width,gallery_ratio_height,gallery_enabled) values(1,'front.png','Front photograph',2,3,0)")
            db.commit()

    def test_editor_includes_private_photographs_and_their_settings(self):
        page = self.client.get('/items/1/edit')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'front.png', page.data)
        self.assertIn(b'Random gallery: 2', page.data)
        self.assertIn(b'gallery_image_ids', page.data)
        self.assertIn(b'/images/1/settings', page.data)
        self.assertIn(b'target="_blank"', page.data)
        self.assertIn(b'Move earlier', page.data)
        self.assertIn(b'Delete selected image', page.data)

    def test_background_delete_stays_on_editor_and_leaves_item_details(self):
        result = self.client.post('/items/1/images/delete', data={
            'csrf_token': 'test-csrf', 'image_id': '1',
        }, headers={'X-Requested-With': 'fetch'})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json['ok'])
        with host.app.app_context():
            db = host.get_db()
            self.assertEqual(db.execute('select name from items where id=1').fetchone()['name'], 'Sentinel')
            self.assertIsNone(db.execute('select id from item_images where id=1').fetchone())

    def test_background_framing_refresh_returns_current_settings(self):
        result = self.client.get('/images/1/settings', headers={'X-Requested-With': 'fetch'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['gallery_ratio_width'], 2)
        self.assertEqual(result.json['gallery_ratio_height'], 3)
        self.assertEqual(result.json['crop_zoom'], 1)

    def test_other_collector_cannot_edit_photos_or_delete_them(self):
        self.login(2)
        self.assertEqual(self.client.get('/items/1/edit').status_code, 403)
        self.assertEqual(self.client.post('/items/1/images/delete', data={
            'csrf_token': 'test-csrf', 'image_id': '1',
        }, headers={'X-Requested-With': 'fetch'}).status_code, 403)
