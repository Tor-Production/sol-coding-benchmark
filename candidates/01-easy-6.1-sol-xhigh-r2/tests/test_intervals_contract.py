import copy
import random
import unittest

from intervals import merge_intervals


class IntervalContractTests(unittest.TestCase):
    def test_overlaps_containment_duplicates_and_adjacency(self):
        ranges = [[9, 12], [2, 4], [1, 10], [2, 4], [13, 13], [20, 21]]
        self.assertEqual(merge_intervals(ranges), [[1, 13], [20, 21]])

    def test_tuples_singletons_and_empty(self):
        self.assertEqual(merge_intervals(()), [])
        self.assertEqual(merge_intervals(((4, 4), [1, 1], (2, 2))), [[1, 2], [4, 4]])

    def test_negative_and_arbitrarily_large_integers(self):
        huge = 10 ** 100
        ranges = [(huge, huge + 1), (-huge, -huge), (-huge + 1, -huge + 2),
                  (huge + 2, huge + 4), (-4, -2), (0, 0)]
        self.assertEqual(merge_intervals(ranges),
                         [[-huge, -huge + 2], [-4, -2], [0, 0], [huge, huge + 4]])

    def test_invalid_outer_containers(self):
        for ranges in (None, 1, True, "", "12", {}, set(), range(2), iter([[1, 2]])):
            with self.subTest(ranges=ranges):
                with self.assertRaises(ValueError):
                    merge_intervals(ranges)

    def test_invalid_intervals(self):
        for interval in (None, 1, "12", {1, 2}, {1: 2}, [], [1], [1, 2, 3],
                         [2, 1], [True, 2], [0, False], [1.0, 2], [1, 2.0],
                         ["1", 2], [1, None], [1, 2 + 0j]):
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    merge_intervals([[0, 0], interval])

    def test_input_and_output_do_not_share_interval_lists(self):
        ranges = [[8, 10], [1, 3], [3, 4], [20, 20]]
        original = copy.deepcopy(ranges)
        result = merge_intervals(ranges)
        self.assertEqual(ranges, original)
        self.assertEqual(result, [[1, 4], [8, 10], [20, 20]])
        for interval in result:
            self.assertIsInstance(interval, list)
            self.assertTrue(all(interval is not source for source in ranges))
        result[0][0] = -100
        self.assertEqual(ranges, original)

    def test_invalid_input_is_not_mutated(self):
        ranges = [[8, 10], [1, 3], [4, 2]]
        original = copy.deepcopy(ranges)
        with self.assertRaises(ValueError):
            merge_intervals(ranges)
        self.assertEqual(ranges, original)

    def test_30000_intervals(self):
        adjacent = [[i, i] for i in range(29999, -1, -1)]
        self.assertEqual(merge_intervals(adjacent), [[0, 29999]])
        disjoint = [(3 * i, 3 * i + 1) for i in range(29999, -1, -1)]
        expected = [[3 * i, 3 * i + 1] for i in range(30000)]
        self.assertEqual(merge_intervals(disjoint), expected)

    def test_against_integer_set_union(self):
        rng = random.Random(0)
        for _ in range(100):
            ranges = [sorted((rng.randint(-20, 20), rng.randint(-20, 20)))
                      for _ in range(rng.randrange(20))]
            points = sorted({point for start, end in ranges
                             for point in range(start, end + 1)})
            expected = []
            for point in points:
                if not expected or point != expected[-1][1] + 1:
                    expected.append([point, point])
                else:
                    expected[-1][1] = point
            self.assertEqual(merge_intervals(ranges), expected)


if __name__ == "__main__":
    unittest.main()
