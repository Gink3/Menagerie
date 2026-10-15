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

    def test_only_first_enabled_photo_is_marked_as_random_gallery_image(self):
        from html.parser import HTMLParser

        class Badges(HTMLParser):
            def __init__(self):
                super().__init__()
                self.visible = 0
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if attrs.get('class') == 'gallery-cover-badge' and 'hidden' not in attrs:
                    self.visible += 1

        with host.app.app_context():
            db = host.get_db()
            db.execute("insert into item_images(item_id,filename,original_name,gallery_enabled,gallery_position) values(1,'second.png','Second',1,1)")
            db.execute("insert into item_images(item_id,filename,original_name,gallery_enabled,gallery_position) values(1,'third.png','Third',1,2)")
            db.commit()
        parser = Badges()
        parser.feed(self.client.get('/items/1/edit').get_data(as_text=True))
        self.assertEqual(parser.visible, 1)

    def test_background_item_save_returns_destination_and_rejects_blank_names(self):
        response = self.client.post('/items/1/edit', data={
            'csrf_token': 'test-csrf', 'name': 'Updated Sentinel',
            'return_to': '/collections/1?view=gallery',
        }, headers={'X-Requested-With': 'fetch'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['ok'])
        self.assertIn('return_to=', response.json['next'])
        response = self.client.post('/items/1/edit', data={
            'csrf_token': 'test-csrf', 'name': '   ',
        }, headers={'X-Requested-With': 'fetch'})
        self.assertEqual(response.status_code, 400)
        with host.app.app_context():
            self.assertEqual(host.get_db().execute('select name from items where id=1').fetchone()['name'], 'Updated Sentinel')

    def test_background_new_item_save_returns_destination(self):
        response = self.client.post('/collections/1/items/new', data={
            'csrf_token': 'test-csrf', 'name': 'New miniature',
        }, headers={'X-Requested-With': 'fetch'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['ok'])
        self.assertTrue(response.json['next'].startswith('/items/'))
