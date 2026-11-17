import unittest

from resume_maker.library import bullet_room


class BulletRoomTest(unittest.TestCase):
    def test_the_first_role_gets_three_and_every_other_role_two(self):
        self.assertEqual(bullet_room(0), 3)
        for index in (1, 2, 3, 4):
            with self.subTest(index):
                self.assertEqual(bullet_room(index), 2)


class AllBulletsTurnTest(unittest.TestCase):
    def test_one_turn_writes_every_job_within_its_room(self):
        from resume_maker import assistant
        rules = assistant.tailor_rules("bullets_all")
        self.assertIn("bullets of\nevery job on the CV", rules)
        self.assertIn("no job gets more bullets than its room", rules)


if __name__ == "__main__":
    unittest.main()
