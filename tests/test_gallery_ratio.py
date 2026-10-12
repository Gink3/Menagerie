from unittest.mock import patch

import app as host
from test_api_keys import DatabaseTest


class GalleryRatioTests(DatabaseTest):
    def test_default_and_saved_ratio_reach_random_layout(self):
        page = self.client.get('/admin/settings')
        self.assertIn(b'name="gallery_ratio_width"', page.data)
        with host.app.app_context(), patch.object(host, 'random_gallery_layout', return_value=[]) as layout:
            host.gallery_images(mixed=True)
            self.assertEqual(layout.call_args.kwargs, {'ratio_width': 1, 'ratio_height': 1})
        response = self.client.post('/admin/settings', data={
            'csrf_token': 'test-csrf', 'site_name': 'Menagerie',
            'default_view': 'table', 'gallery_ratio_width': '3', 'gallery_ratio_height': '2',
        })
        self.assertEqual(response.status_code, 200)
        with host.app.app_context(), patch.object(host, 'random_gallery_layout', return_value=[]) as layout:
            host.gallery_images(mixed=True)
            self.assertEqual(layout.call_args.kwargs, {'ratio_width': 3, 'ratio_height': 2})

    def test_invalid_ratio_does_not_save_settings(self):
        for invalid in ('0', '-1', '7', '1.5', 'abc'):
            response = self.client.post('/admin/settings', data={
                'csrf_token': 'test-csrf', 'site_name': 'Changed',
                'gallery_ratio_width': invalid, 'gallery_ratio_height': '1',
            })
            self.assertEqual(response.status_code, 400)
        with host.app.app_context():
            self.assertIsNone(host.get_db().execute(
                "select value from settings where key='gallery_ratio_width'"
            ).fetchone())

    def test_collector_cannot_change_global_ratio(self):
        self.login(2)
        response = self.client.post('/admin/settings', data={
            'csrf_token': 'test-csrf', 'gallery_ratio_width': '2', 'gallery_ratio_height': '1',
        })
        self.assertEqual(response.status_code, 403)
