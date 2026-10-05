import unittest

from intervals import merge_intervals


class AdditionalTests(unittest.TestCase):
    def test_invalid_inputs(self):
        for ranges in (None, 1, "", {}, iter([]), [None], [[1]],
                       [[1, 2, 3]], [[True, 2]], [[1, False]],
                       [[1.0, 2]], [[1, "2"]], [[2, 1]]):
            with self.subTest(ranges=ranges):
                with self.assertRaises(ValueError):
                    merge_intervals(ranges)

    def test_nested_duplicate_negative_and_large(self):
        large = 10 ** 100
        ranges = ((large + 1, large + 2), [-5, -2], (-4, -3),
                  [-1, 0], [-5, -2], (large, large))
        self.assertEqual(merge_intervals(ranges),
                         [[-5, 0], [large, large + 2]])

    def test_does_not_mutate_or_alias(self):
        ranges = [[8, 10], [1, 3], [4, 5]]
        result = merge_intervals(ranges)
        self.assertEqual(ranges, [[8, 10], [1, 3], [4, 5]])
        result[0][0] = 99
        self.assertEqual(ranges, [[8, 10], [1, 3], [4, 5]])

    def test_thirty_thousand_intervals(self):
        ranges = [(i, i) for i in reversed(range(30000))]
        self.assertEqual(merge_intervals(ranges), [[0, 29999]])

    def test_empty_tuple(self):
        self.assertEqual(merge_intervals(()), [])
