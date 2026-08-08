import unittest

from flask import session

from app import (
    add_security_headers,
    app,
    clear_stale_login_session,
    sanitize_svg,
    valid_annotation_geometry,
    valid_json_object,
    wiki_slug,
)


class Phase11SecurityTests(unittest.TestCase):
    def test_svg_rejects_scripts(self):
        with self.assertRaises(ValueError):
            sanitize_svg('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')

    def test_svg_rejects_external_references(self):
        with self.assertRaises(ValueError):
            sanitize_svg('<svg xmlns="http://www.w3.org/2000/svg"><use href="https://example.com/a.svg"/></svg>')

    def test_annotation_coordinates_are_normalized(self):
        self.assertEqual(valid_annotation_geometry("point", '{"x":0.5,"y":0.25}')['x'], 0.5)
        with self.assertRaises(ValueError):
            valid_annotation_geometry("rectangle", '{"x":-0.1,"y":0.2,"width":0.5,"height":0.5}')

    def test_ai_metadata_must_be_an_object(self):
        self.assertEqual(valid_json_object('{"epochs":10}', "invalid"), '{"epochs": 10}')
        with self.assertRaises(ValueError):
            valid_json_object('[1, 2]', "invalid")

    def test_wiki_slugs_are_stable_and_safe(self):
        self.assertEqual(wiki_slug("House of Dawn!"), "house-of-dawn")
        self.assertEqual(wiki_slug("!!!"), "page")

    def test_html_responses_are_not_cached(self):
        with app.test_request_context("/login"):
            response = add_security_headers(app.make_response("Sign in"))
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_stale_login_session_is_cleared_from_common_paths(self):
        with app.test_request_context("/login"):
            session["csrf_token"] = "stale"
            response = clear_stale_login_session()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")
        cookie_headers = response.headers.getlist("Set-Cookie")
        self.assertTrue(any(header.endswith("Path=/") for header in cookie_headers))
        self.assertTrue(any(header.endswith("Path=/login") for header in cookie_headers))


if __name__ == "__main__":
    unittest.main()
