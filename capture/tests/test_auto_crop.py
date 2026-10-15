import unittest
from PIL import Image, ImageDraw
from mini_scan.editing import plain_background_crop


class AutoCropTests(unittest.TestCase):
    def photo(self, background="white", subject="black"):
        image = Image.new("RGB", (400, 300), background)
        ImageDraw.Draw(image).rectangle((150, 50, 249, 249), fill=subject)
        return image

    def test_bounds_padding_and_original_preserved(self):
        image = self.photo()
        before = image.tobytes()
        self.assertEqual(plain_background_crop(image, padding=0), (150, 50, 250, 250))
        self.assertEqual(plain_background_crop(image), (130, 30, 270, 270))
        self.assertEqual(image.tobytes(), before)

    def test_dark_and_colored_backgrounds(self):
        for color in ("black", (35, 70, 95)):
            self.assertEqual(plain_background_crop(self.photo(color, "white"), padding=0), (150, 50, 250, 250))

    def test_preserves_enclosed_background_color_and_thin_detail(self):
        image = self.photo()
        draw = ImageDraw.Draw(image)
        draw.rectangle((175, 100, 225, 200), fill="white")
        draw.line((200, 50, 300, 25), fill="black", width=2)
        box = plain_background_crop(image, padding=0)
        self.assertGreaterEqual(box[2], 300)
        self.assertLessEqual(box[1], 25)

    def test_noise_speck_is_ignored_and_padding_clamps(self):
        image = self.photo()
        image.putpixel((5, 5), (0, 0, 0))
        self.assertEqual(plain_background_crop(image, padding=.5), (50, 0, 350, 300))

    def test_rejects_empty_nonuniform_and_invalid_parameters(self):
        image = Image.new("RGB", (400, 300), "white")
        with self.assertRaises(ValueError):
            plain_background_crop(image)
        ImageDraw.Draw(image).rectangle((0, 0, 199, 299), fill="black")
        with self.assertRaises(ValueError):
            plain_background_crop(image)
        for tolerance, padding in [(0, .1), (81, .1), (30, -.1), (float('nan'), .1)]:
            with self.assertRaises(ValueError):
                plain_background_crop(self.photo(), tolerance, padding)

    def test_downsampled_bounds_map_to_original_pixels(self):
        image = self.photo().resize((4000, 3000))
        box = plain_background_crop(image, padding=0)
        for actual, expected in zip(box, (1500, 500, 2500, 2500)):
            self.assertLess(abs(actual - expected), 10)

    def test_editor_only_suggests_until_apply_and_keeps_crop_on_failure(self):
        from unittest.mock import Mock
        from mini_scan.app import ImageEditor
        editor = ImageEditor.__new__(ImageEditor)
        editor.image = self.photo()
        editor.background_tolerance = Mock(); editor.background_tolerance.get.return_value = "30"
        editor.crop_padding = Mock(); editor.crop_padding.get.return_value = "10"
        editor.crop_message = Mock()
        editor.set_crop = Mock()
        editor.dialog = Mock()
        editor.auto_crop()
        editor.set_crop.assert_called_once_with((130, 30, 270, 270))
        editor.dialog.draft.edit.assert_not_called()
        editor.set_crop.reset_mock()
        editor.background_tolerance.get.return_value = "invalid"
        editor.auto_crop()
        editor.set_crop.assert_not_called()
        editor.dialog.draft.edit.assert_not_called()
