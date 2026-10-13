import app as host
from test_api_keys import DatabaseTest


class GalleryRatioTests(DatabaseTest):
    def setUp(self):
        super().setUp()
        with host.app.app_context():
            db = host.get_db()
            db.execute("insert into collections(name,owner_id) values('Minis',1)")
            db.execute("insert into items(collection_id,name) values(1,'Sentinel')")
            for filename in ('front.png', 'side.png'):
                db.execute(
                    "insert into item_images(item_id,filename,original_name) values(1,?,?)",
                    (filename, filename),
                )
            db.commit()

    def save_ratio(self, image_id=1, **values):
        return self.client.post(f'/images/{image_id}/settings', data={
            'csrf_token': 'test-csrf', **values,
        }, headers={'X-Requested-With': 'fetch'})

    def test_default_and_independent_ratios_for_photos_of_the_same_item(self):
        page = self.client.get('/images/1/settings')
        self.assertIn(b'name="gallery_ratio_width"', page.data)
        self.assertNotIn(b'name="gallery_ratio_width"', self.client.get('/admin/settings').data)
        with host.app.app_context():
            tiles = host.gallery_images(mixed=True)
            self.assertEqual(len(tiles), 2)
            self.assertTrue(all(tile['gallery_w'] == tile['gallery_h'] for tile in tiles))
        self.assertEqual(self.save_ratio(gallery_ratio_width='3', gallery_ratio_height='2').status_code, 200)
        self.assertEqual(self.save_ratio(2, gallery_ratio_width='1', gallery_ratio_height='2').status_code, 200)
        with host.app.app_context():
            tiles = host.gallery_images(mixed=True)
            self.assertEqual(len(tiles), 2)
            for tile in tiles:
                self.assertEqual(len(tile['images']), 1)
                image = tile['images'][0]
                width, height = (3, 2) if image['id'] == 1 else (1, 2)
                self.assertEqual(tile['gallery_w'] * height, tile['gallery_h'] * width)
            personal = host.gallery_images(owner_id=1)
            self.assertEqual(len(personal), 1)
            self.assertEqual(len(personal[0]['images']), 2)

    def test_invalid_ratio_does_not_change_ratio_or_crop(self):
        for key in ('gallery_ratio_width', 'gallery_ratio_height'):
            for invalid in ('0', '-1', '7', '1.5', 'abc', ''):
                response = self.save_ratio(crop_x='75', **{key: invalid})
                self.assertEqual(response.status_code, 400)
        with host.app.app_context():
            image = host.get_db().execute('select * from item_images where id=1').fetchone()
            self.assertEqual(image['gallery_ratio_width'], 1)
            self.assertEqual(image['gallery_ratio_height'], 1)
            self.assertEqual(image['crop_x'], 50)

    def test_existing_crop_requests_preserve_image_ratio(self):
        self.save_ratio(gallery_ratio_width='2', gallery_ratio_height='3')
        self.assertEqual(self.save_ratio(crop_x='25', crop_zoom='1.5').status_code, 200)
        with host.app.app_context():
            image = host.get_db().execute('select * from item_images where id=1').fetchone()
            self.assertEqual((image['gallery_ratio_width'], image['gallery_ratio_height']), (2, 3))
            self.assertEqual((image['crop_x'], image['crop_zoom']), (25, 1.5))

    def test_other_collector_cannot_change_image_ratio(self):
        self.login(2)
        self.assertEqual(self.save_ratio(gallery_ratio_width='2').status_code, 403)
        self.assertEqual(self.client.get('/images/1/settings').status_code, 403)

    def test_old_global_setting_does_not_override_image_defaults(self):
        with host.app.app_context():
            db = host.get_db()
            db.execute("insert into settings(key,value) values('gallery_ratio_width','3')")
            db.execute("insert into settings(key,value) values('gallery_ratio_height','2')")
            db.commit()
            for tile in host.gallery_images(mixed=True):
                self.assertEqual(tile['gallery_w'], tile['gallery_h'])

    def test_existing_database_receives_square_defaults_without_changing_crop(self):
        with host.app.app_context():
            db = host.get_db()
            db.execute('alter table item_images drop column gallery_ratio_width')
            db.execute('alter table item_images drop column gallery_ratio_height')
            db.execute('update item_images set crop_x=25, crop_zoom=1.5 where id=1')
            db.commit()
            host.init_db()
            image = db.execute('select * from item_images where id=1').fetchone()
            self.assertEqual((image['gallery_ratio_width'], image['gallery_ratio_height']), (1, 1))
            self.assertEqual((image['crop_x'], image['crop_zoom']), (25, 1.5))
