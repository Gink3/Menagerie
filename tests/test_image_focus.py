import app as host
from test_api_keys import DatabaseTest


class ImageFocusTests(DatabaseTest):
    def setUp(self):
        super().setUp()
        with host.app.app_context():
            db = host.get_db()
            db.execute("insert into collections(name,owner_id) values('Minis',1)")
            db.execute("insert into items(collection_id,name) values(1,'Sentinel')")
            for filename in ('front.png','side.png'):
                db.execute('insert into item_images(item_id,filename,original_name) values(1,?,?)',(filename,filename))
            db.commit()

    def save(self, **values):
        return self.client.post('/images/1/settings', data={
            'csrf_token':'test-csrf', **values,
        }, headers={'X-Requested-With':'fetch'})

    def test_manual_point_is_saved_per_image_and_used_by_gallery(self):
        result = self.save(focal_mode='manual',focal_x='50',focal_y='25',crop_zoom='3')
        self.assertEqual(result.status_code,200)
        data = self.client.get('/images/1/settings',headers={'X-Requested-With':'fetch'}).json
        self.assertEqual((data['focal_x'],data['focal_y'],data['crop_zoom']),(50,25,3))
        page = self.client.get('/')
        self.assertIn(b'data-focal-x="50.0"',page.data)
        self.assertIn(b'data-focal-y="25.0"',page.data)
        with host.app.app_context():
            other = host.get_db().execute('select focal_x,focal_y from item_images where id=2').fetchone()
            self.assertEqual(tuple(other),(None,None))

    def test_invalid_point_does_not_change_saved_crop(self):
        for invalid in ('-1','101','nan','inf','abc',''):
            result = self.save(focal_mode='manual',focal_x=invalid,focal_y='25',crop_zoom='3')
            self.assertEqual(result.status_code,400)
        with host.app.app_context():
            image = host.get_db().execute('select * from item_images where id=1').fetchone()
            self.assertIsNone(image['focal_x'])
            self.assertEqual(image['crop_zoom'],1)

    def test_legacy_crop_request_preserves_point_and_reset_clears_it(self):
        self.save(focal_mode='manual',focal_x='60',focal_y='25')
        self.save(crop_zoom='2')
        data = self.client.get('/images/1/settings',headers={'X-Requested-With':'fetch'}).json
        self.assertEqual((data['focal_x'],data['focal_y']),(60,25))
        self.assertEqual(self.save(focal_mode='legacy',crop_x='50',crop_y='50',crop_zoom='1').status_code,200)
        data = self.client.get('/images/1/settings',headers={'X-Requested-With':'fetch'}).json
        self.assertEqual((data['focal_x'],data['focal_y']),(None,None))

    def test_other_collector_cannot_set_point(self):
        self.login(2)
        self.assertEqual(self.save(focal_mode='manual',focal_x='50',focal_y='25').status_code,403)
