import unittest
from intervals import merge_intervals


class PublicTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(merge_intervals([[8, 10], [1, 3], [3, 4]]), [[1, 4], [8, 10]])

    def test_adjacent(self):
        self.assertEqual(merge_intervals([[1, 2], [3, 5]]), [[1, 5]])

    def test_empty_and_validation(self):
        self.assertEqual(merge_intervals([]), [])
        with self.assertRaises(ValueError):
            merge_intervals([[3, 1]])
