import copy
import random
import unittest

from intervals import merge_intervals


class AdditionalTests(unittest.TestCase):
    def test_containment_duplicates_and_adjacency_chain(self):
        ranges = [[8, 9], [1, 6], [2, 3], [1, 6], [7, 7], [12, 12]]
        self.assertEqual(merge_intervals(ranges), [[1, 9], [12, 12]])

    def test_tuple_and_mixed_containers(self):
        self.assertEqual(merge_intervals(()), [])
        self.assertEqual(
            merge_intervals(((5, 5), [0, 0], (2, 4), [1, 1])), [[0, 5]]
        )

    def test_negative_and_arbitrarily_large_integers(self):
        huge = 10**1000
        self.assertEqual(
            merge_intervals(
                [[huge + 1, huge + 2], [-5, -3], [-2, 0], [huge, huge]]
            ),
            [[-5, 0], [huge, huge + 2]],
        )
        self.assertEqual(
            merge_intervals([[-huge, -huge], [-huge + 1, -huge + 2]]),
            [[-huge, -huge + 2]],
        )

    def test_invalid_outer_containers(self):
        for ranges in (None, 1, True, "", "12", {}, set(), iter([])):
            with self.subTest(ranges=ranges):
                with self.assertRaises(ValueError):
                    merge_intervals(ranges)

    def test_invalid_intervals(self):
        invalid = (
            None, 1, "12", {1, 2}, {0: 1, 1: 2}, [], [1], [1, 2, 3],
            [2, 1], [True, 2], [0, False], [1.0, 2], [1, 2.0],
            ["1", 2], [1, None], [[1], 2],
        )
        for interval in invalid:
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    merge_intervals([[0, 0], interval])

    def test_input_and_output_are_independent(self):
        ranges = [[8, 10], [1, 3], [3, 4]]
        original = copy.deepcopy(ranges)
        result = merge_intervals(ranges)
        self.assertEqual(ranges, original)
        self.assertTrue(all(isinstance(interval, list) for interval in result))
        result[0][0] = -100
        self.assertEqual(ranges, original)
        ranges[0][1] = 100
        self.assertEqual(result[1], [8, 10])

    def test_validation_does_not_mutate_input(self):
        ranges = [[8, 10], [1, 3], [True, 4]]
        original = copy.deepcopy(ranges)
        with self.assertRaises(ValueError):
            merge_intervals(ranges)
        self.assertEqual(ranges, original)

    def test_randomized_union(self):
        rng = random.Random(42)
        for _ in range(200):
            ranges = [
                sorted((rng.randint(-20, 20), rng.randint(-20, 20)))
                for _ in range(rng.randrange(20))
            ]
            points = sorted({
                point for start, end in ranges for point in range(start, end + 1)
            })
            expected = []
            for point in points:
                if expected and point == expected[-1][1] + 1:
                    expected[-1][1] = point
                else:
                    expected.append([point, point])
            self.assertEqual(merge_intervals(ranges), expected)

    def test_thirty_thousand_intervals(self):
        ranges = [[index, index] for index in range(29999, -1, -1)]
        self.assertEqual(merge_intervals(ranges), [[0, 29999]])
        disjoint = [[index * 2, index * 2] for index in range(29999, -1, -1)]
        self.assertEqual(merge_intervals(disjoint), list(reversed(disjoint)))
