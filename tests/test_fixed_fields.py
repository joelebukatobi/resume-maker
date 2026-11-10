import unittest

from resume_maker.assistant import FIXED_CV_FIELD


class FixedFieldsTest(unittest.TestCase):
    def test_structure_is_fixed_and_lines_are_not(self):
        for path in ("name", "contact.0.text", "sections.2.heading",
                     "sections.2.entries.0.left", "sections.2.entries.0.sub_right"):
            with self.subTest(path):
                self.assertTrue(FIXED_CV_FIELD.match(path))
        for path in ("sections.2.entries.0.bullets.1", "sections.0.text", "sections.1.items.0.text"):
            with self.subTest(path):
                self.assertFalse(FIXED_CV_FIELD.match(path))


if __name__ == "__main__":
    unittest.main()
