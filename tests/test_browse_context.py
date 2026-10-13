import app as host
from test_api_keys import DatabaseTest


class BrowseContextTests(DatabaseTest):
    def setUp(self):
        super().setUp()
        with host.app.app_context():
            db = host.get_db()
            db.execute("insert into collections(name,owner_id) values('Minis',1)")
            db.execute("insert into items(collection_id,name) values(1,'Sentinel')")
            db.commit()

    def test_filter_context_survives_item_edit_and_save(self):
        context = '/collections/1?view=gallery&q=Sentinel&sort=created_at&dir=desc'
        page = self.client.get('/items/1', query_string={'return_to': context})
        self.assertIn(b'data-collection-back href="/collections/1?view=gallery&amp;q=Sentinel', page.data)
        page = self.client.get('/items/1/edit', query_string={'return_to': context})
        self.assertIn(b'name="return_to" value="/collections/1?view=gallery', page.data)
        result = self.client.post('/items/1/edit', data={'csrf_token': 'test-csrf', 'name': 'Sentinel', 'return_to': context})
        self.assertIn('return_to=', result.location)
        page = self.client.get(result.location)
        self.assertIn(b'data-collection-back href="/collections/1?view=gallery&amp;q=Sentinel', page.data)

    def test_unrelated_and_external_return_targets_are_discarded(self):
        for target in ('https://evil.example/collections/1', '//evil.example/collections/1', '/collections/2', '/logout', '/collections/1\\evil', '/collections/1\n'):
            with self.subTest(target=target):
                page = self.client.get('/items/1', query_string={'return_to': target})
                self.assertIn(b'data-collection-back href="/collections/1"', page.data)

    def test_finishing_framing_returns_to_shared_photo_editor_with_context(self):
        with host.app.app_context():
            db = host.get_db()
            db.execute("insert into item_images(item_id,filename,original_name) values(1,'front.png','Front')")
            db.commit()
        result = self.client.post('/images/1/settings', data={
            'csrf_token': 'test-csrf', 'finish_framing': '1',
            'return_to': '/collections/1?view=gallery',
        }, headers={'X-Requested-With': 'fetch'})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json['next'].startswith('/items/1/edit?'))
        self.assertTrue(result.json['next'].endswith('#photographs'))
