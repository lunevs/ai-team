import unittest
from app import calculate

class ResultTest(unittest.TestCase):
    def test_result(self):
        self.assertEqual(calculate(), {'value':42,'unit':'synthetic-check'})
