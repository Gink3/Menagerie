import app as host
from test_api_keys import DatabaseTest


class TagEditorTests(DatabaseTest):
    def setUp(self):
        super().setUp()
        with host.app.app_context():
            db = host.get_db()
            for owner, name in ((1, 'Own'), (2, 'Other')):
                cid = db.execute('insert into collections(name,owner_id) values(?,?)', (name,owner)).lastrowid
                iid = db.execute('insert into items(collection_id,name) values(?,?)', (cid,name)).lastrowid
                host.replace_item_tags(iid, ['painted', 'steel'] if owner == 1 else ['private-project'])
            db.commit()

    def test_known_tags_are_scoped_to_collection_owner(self):
        with host.app.app_context():
            self.assertEqual(host.get_known_tags(1), ['painted', 'steel'])
            self.assertEqual(host.get_known_tags(2), ['private-project'])
        page = self.client.get('/collections/1/items/new')
        self.assertIn(b'painted', page.data)
        self.assertNotIn(b'private-project', page.data)
        self.login(2)
        page = self.client.get('/collections/2/items/new')
        self.assertIn(b'private-project', page.data)
        self.assertNotIn(b'steel', page.data)

    def test_new_tags_normalize_and_are_available_after_saving(self):
        response = self.client.post('/collections/1/items/new', data={
            'csrf_token': 'test-csrf', 'name': 'New miniature', 'tags': 'Painted,  northern guard, painted, Steel',
        })
        self.assertEqual(response.status_code, 302)
        with host.app.app_context():
            item = host.get_db().execute("select id from items where name='New miniature'").fetchone()
            self.assertEqual(host.get_item_tags(item['id']), ['northern guard','painted','steel'])
            self.assertIn('northern guard', host.get_known_tags(1))
            self.assertNotIn('northern guard', host.get_known_tags(2))

    def test_removing_all_tags_does_not_affect_other_items(self):
        self.client.post('/items/1/edit', data={
            'csrf_token': 'test-csrf', 'name': 'Own', 'tags': '',
        })
        with host.app.app_context():
            self.assertEqual(host.get_item_tags(1), [])
            self.assertEqual(host.get_item_tags(2), ['private-project'])
