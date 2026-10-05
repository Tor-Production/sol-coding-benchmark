import unittest

from intervals import merge_intervals


class ExtendedTests(unittest.TestCase):
    def test_merge_and_preserve_input(self):
        ranges = [[5, 7], (1, 2), [3, 4], [6, 9], [1, 2], [-3, -2]]
        self.assertEqual(merge_intervals(ranges), [[-3, -2], [1, 9]])
        self.assertEqual(ranges, [[5, 7], (1, 2), [3, 4], [6, 9], [1, 2], [-3, -2]])

    def test_malformed_input(self):
        for ranges in (None, {}, 3, [[True, 2]], [[1, False]],
                       [[2, 1]], [[1]], [[1, 2, 3]], [[1.0, 2]]):
            with self.subTest(ranges=ranges), self.assertRaises(ValueError):
                merge_intervals(ranges)

    def test_large_input(self):
        ranges = [[2 * i, 2 * i] for i in range(29999, -1, -1)]
        result = merge_intervals(ranges)
        self.assertEqual(len(result), 30000)
        self.assertEqual((result[0], result[-1]), ([0, 0], [59998, 59998]))
        self.assertEqual(ranges[0], [59998, 59998])
