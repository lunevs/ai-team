import unittest
from app import result

class BaseTest(unittest.TestCase):
    def test_original_result(self):
        self.assertEqual(result(),{'value':42,'unit':'synthetic-check'})
