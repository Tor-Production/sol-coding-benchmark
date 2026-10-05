import copy
import unittest

from intervals import merge_intervals


class AdditionalTests(unittest.TestCase):
    def test_invalid_outer_containers(self):
        for ranges in (None, 3, "12", {1: 2}, {1, 2}, iter([[1, 2]])):
            with self.subTest(ranges=ranges):
                with self.assertRaises(ValueError):
                    merge_intervals(ranges)

    def test_invalid_intervals(self):
        for interval in (None, 3, "12", {1: 2}, [], [1], [1, 2, 3],
                         [True, 2], [1, False], [1.0, 2], [1, 2.0],
                         ["1", 2], [1, None], [2, 1]):
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    merge_intervals([[0, 0], interval])

    def test_containment_duplicates_and_transitive_adjacency(self):
        self.assertEqual(
            merge_intervals([[10, 12], [3, 4], [1, 2], [2, 2],
                             [1, 2], [5, 8], [9, 9], [15, 15]]),
            [[1, 12], [15, 15]],
        )

    def test_negative_and_arbitrarily_large_integers(self):
        huge = 10 ** 100
        self.assertEqual(
            merge_intervals(((huge + 1, huge + 2), [-huge, -huge],
                             (-huge + 1, -1), (huge, huge))),
            [[-huge, -1], [huge, huge + 2]],
        )
        self.assertEqual(merge_intervals(()), [])

    def test_input_and_output_do_not_share_mutable_intervals(self):
        ranges = [[8, 10], [1, 3], [3, 5]]
        original = copy.deepcopy(ranges)
        result = merge_intervals(ranges)
        self.assertEqual(ranges, original)
        result[0][0] = -100
        self.assertEqual(ranges, original)

    def test_thirty_thousand_intervals(self):
        adjacent = [(i, i) for i in range(29999, -1, -1)]
        self.assertEqual(merge_intervals(adjacent), [[0, 29999]])
        separated = [(3 * i, 3 * i + 1) for i in range(29999, -1, -1)]
        self.assertEqual(merge_intervals(separated),
                         [[3 * i, 3 * i + 1] for i in range(30000)])
