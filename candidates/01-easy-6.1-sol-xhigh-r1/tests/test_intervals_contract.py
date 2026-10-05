import copy
import unittest

from intervals import merge_intervals


class IntervalContractTests(unittest.TestCase):
    def test_tuple_input_containment_and_duplicates(self):
        ranges = ((9, 12), [2, 4], (1, 7), [2, 4], (8, 8), [20, 20])
        self.assertEqual(merge_intervals(ranges), [[1, 12], [20, 20]])
        self.assertEqual(merge_intervals(()), [])

    def test_negative_and_arbitrarily_large_integers(self):
        large = 10**200
        ranges = [
            [large + 1, large + 5],
            [-large, -large + 2],
            [large, large],
            [-large + 3, -large + 4],
            [-2, -1],
            [0, 0],
        ]
        self.assertEqual(
            merge_intervals(ranges),
            [[-large, -large + 4], [-2, 0], [large, large + 5]],
        )

    def test_invalid_outer_containers(self):
        for ranges in (None, False, 3, "", "12", {}, set(), iter([])):
            with self.subTest(ranges=ranges):
                with self.assertRaises(ValueError):
                    merge_intervals(ranges)

    def test_invalid_intervals(self):
        invalid = (
            None, 1, "12", {1, 2}, {"start": 1, "end": 2},
            [], [1], [1, 2, 3], [2, 1],
            [True, 2], [0, False], [1.0, 2], [1, 2.0],
            ["1", 2], [1, None], [[], 2],
        )
        for interval in invalid:
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    merge_intervals([[0, 0], interval])

    def test_input_is_preserved_and_output_is_independent(self):
        ranges = [[8, 10], [1, 2], [3, 7], [1, 2]]
        original = copy.deepcopy(ranges)
        result = merge_intervals(ranges)
        self.assertEqual(result, [[1, 10]])
        self.assertEqual(ranges, original)
        result[0][0] = -100
        self.assertEqual(ranges, original)

        separate = [[8, 8], [1, 1]]
        result = merge_intervals(separate)
        result[0][0] = -100
        self.assertEqual(separate, [[8, 8], [1, 1]])

    def test_invalid_input_is_preserved(self):
        ranges = [[8, 10], [1, 2], [4, 3]]
        original = copy.deepcopy(ranges)
        with self.assertRaises(ValueError):
            merge_intervals(ranges)
        self.assertEqual(ranges, original)

    def test_thirty_thousand_intervals(self):
        count = 30_000
        disjoint = [[3 * i, 3 * i + 1] for i in range(count - 1, -1, -1)]
        self.assertEqual(merge_intervals(disjoint), list(reversed(disjoint)))
        adjacent = [(i, i) for i in range(count - 1, -1, -1)]
        self.assertEqual(merge_intervals(adjacent), [[0, count - 1]])
