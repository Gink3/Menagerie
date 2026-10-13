import app as host
from test_api_keys import DatabaseTest


class CollectionFieldEditorTests(DatabaseTest):
    def setUp(self):
        super().setUp()
        with host.app.app_context():
            db = host.get_db()
            db.execute("insert into collections(name,owner_id) values('Minis',2)")
            db.execute("insert into collections(name,owner_id) values('Other',1)")
            db.execute("insert into fields(collection_id,name,field_type,position) values(1,'Manufacturer','text',0)")
            db.execute("insert into fields(collection_id,name,field_type,position) values(1,'Painted','checkbox',1)")
            db.execute("insert into fields(collection_id,name,field_type,position) values(2,'Other field','text',0)")
            db.execute("insert into items(collection_id,name) values(1,'Sentinel')")
            db.execute("insert into item_values(item_id,field_id,value) values(1,1,'Studio')")
            db.commit()

    def test_admin_saves_field_removal_and_position_together(self):
        result = self.client.post('/collections/1/edit', data={
            'csrf_token': 'test-csrf', 'name': 'Minis', 'delete_field_ids': '1',
            'field_id': '2', 'field_name': 'Painted', 'field_type': 'checkbox',
            'field_options': '', 'field_position_2': '0',
            'return_to': '/collections/1?view=gallery',
        })
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result.location, '/collections/1?view=gallery')
        with host.app.app_context():
            db = host.get_db()
            self.assertIsNone(db.execute('select id from fields where id=1').fetchone())
            self.assertIsNone(db.execute('select value from item_values where field_id=1').fetchone())
            self.assertEqual(db.execute('select position from fields where id=2').fetchone()['position'], 0)
            self.assertIsNotNone(db.execute('select id from fields where id=3').fetchone())

    def test_collector_cannot_use_admin_only_removal_or_position_controls(self):
        self.login(2)
        page = self.client.get('/collections/1/edit')
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b'data-remove-existing-field="1"', page.data)
        for action in ({'delete_field_ids': '1'}, {'field_position_1': '2'}):
            result = self.client.post('/collections/1/edit', data={
                'csrf_token': 'test-csrf', 'name': 'Changed', **action,
            })
            self.assertEqual(result.status_code, 403)
        with host.app.app_context():
            self.assertEqual(host.get_db().execute('select name from collections where id=1').fetchone()['name'], 'Minis')

    def test_invalid_removals_and_positions_do_not_change_collection(self):
        for action in ({'delete_field_ids': '3'}, {'delete_field_ids': 'abc'}, {'field_position_1': '-1'}):
            result = self.client.post('/collections/1/edit', data={
                'csrf_token': 'test-csrf', 'name': 'Changed', **action,
            })
            self.assertEqual(result.status_code, 400)
        with host.app.app_context():
            db = host.get_db()
            self.assertEqual(db.execute('select name from collections where id=1').fetchone()['name'], 'Minis')
            self.assertEqual(db.execute('select count(*) as total from fields').fetchone()['total'], 3)

    def test_admin_fields_links_to_single_editor(self):
        page = self.client.get('/admin/fields?collection_id=1')
        self.assertIn(b'/collections/1/edit#fields', page.data)
        self.assertNotIn(b'name="field_type"', page.data)
        self.assertNotIn(b'class="field-row"', page.data)
