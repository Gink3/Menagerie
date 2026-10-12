import unittest

from flask import session
from werkzeug.exceptions import NotFound
from werkzeug.datastructures import MultiDict

from app import (
    add_security_headers,
    apply,
    app,
    clear_stale_login_session,
    form_collection_field_rows,
    form_field_definitions,
    random_gallery_layout,
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

    def test_collection_field_definitions_keep_names_and_types_paired(self):
        fields = form_field_definitions(MultiDict([
            ("field_name", "Manufacturer"),
            ("field_type", "text"),
            ("field_name", "Complete"),
            ("field_type", "checkbox"),
            ("field_name", ""),
            ("field_type", "date"),
        ]))
        self.assertEqual(fields, [("Manufacturer", "text"), ("Complete", "checkbox")])

    def test_collection_field_rows_keep_existing_field_ids_paired(self):
        fields = form_collection_field_rows(MultiDict([
            ("field_id", "12"),
            ("field_name", "Manufacturer"),
            ("field_type", "text"),
            ("field_id", ""),
            ("field_name", "Condition"),
            ("field_type", "checkbox"),
        ]))
        self.assertEqual(fields, [("12", "Manufacturer", "text"), ("", "Condition", "checkbox")])

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

    def test_session_cookie_has_an_application_specific_name(self):
        self.assertEqual(app.config["SESSION_COOKIE_NAME"], "menagerie_session_v2")

    def test_public_registration_route_is_disabled(self):
        with app.test_request_context("/apply"):
            with self.assertRaises(NotFound):
                apply()

    def test_random_gallery_layout_defaults_to_square_units_without_overlap(self):
        tiles = random_gallery_layout([{"id": index} for index in range(12)])
        occupied = set()
        for tile in tiles:
            self.assertEqual(tile["gallery_w"], tile["gallery_h"])
            self.assertLessEqual(tile["gallery_x"] + tile["gallery_w"], 6)
            for y in range(tile["gallery_y"], tile["gallery_y"] + tile["gallery_h"]):
                for x in range(tile["gallery_x"], tile["gallery_x"] + tile["gallery_w"]):
                    self.assertNotIn((x, y), occupied)
                    occupied.add((x, y))

    def test_random_gallery_layout_preserves_configured_ratio(self):
        for width, height in ((2, 1), (1, 2), (4, 3), (6, 6)):
            tiles = random_gallery_layout(
                [{"id": index} for index in range(20)],
                ratio_width=width, ratio_height=height,
            )
            occupied = set()
            for tile in tiles:
                self.assertEqual(tile["gallery_w"] * height, tile["gallery_h"] * width)
                self.assertLessEqual(tile["gallery_x"] + tile["gallery_w"], 6)
                for y in range(tile["gallery_y"], tile["gallery_y"] + tile["gallery_h"]):
                    for x in range(tile["gallery_x"], tile["gallery_x"] + tile["gallery_w"]):
                        self.assertNotIn((x, y), occupied)
                        occupied.add((x, y))



if __name__ == "__main__":
    unittest.main()
