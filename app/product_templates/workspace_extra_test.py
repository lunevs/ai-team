import unittest
from app import extra_result

class ExtraTest(unittest.TestCase):
    def test_added_result(self):
        self.assertEqual(extra_result(),{'value':43,'unit':'synthetic-check'})
