import copy
import random
import unittest

from intervals import merge_intervals


class ContractTests(unittest.TestCase):
    def test_empty_containers(self):
        self.assertEqual(merge_intervals([]), [])
        self.assertEqual(merge_intervals(()), [])

    def test_overlapping_contained_duplicate_and_adjacent(self):
        ranges = [[8, 10], [2, 4], [1, 6], [3, 3], [1, 6], [7, 7], [12, 13]]
        self.assertEqual(merge_intervals(ranges), [[1, 10], [12, 13]])

    def test_mixed_lists_and_tuples(self):
        self.assertEqual(
            merge_intervals(((5, 8), [-2, 0], (1, 1), [4, 4])),
            [[-2, 1], [4, 8]],
        )

    def test_arbitrarily_large_positive_and_negative_integers(self):
        bound = 10**200
        ranges = [
            [bound + 3, bound + 10],
            [-bound + 2, -bound + 3],
            [bound, bound + 2],
            [-bound, -bound + 1],
        ]
        self.assertEqual(
            merge_intervals(ranges),
            [[-bound, -bound + 3], [bound, bound + 10]],
        )

    def test_invalid_outer_containers(self):
        invalid = [None, 42, True, "12", {"start": 1}, {(1, 2)}, range(2), iter([])]
        for ranges in invalid:
            with self.subTest(container_type=type(ranges).__name__):
                with self.assertRaises(ValueError):
                    merge_intervals(ranges)

    def test_malformed_intervals(self):
        invalid = [None, 1, "12", [], [1], [1, 2, 3], range(2), {1, 2}, {1: 2, 3: 4}]
        for interval in invalid:
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    merge_intervals([[0, 0], interval])

    def test_invalid_endpoints(self):
        invalid = [True, False, 1.0, "1", None, 1 + 0j, [], (1,)]
        for endpoint in invalid:
            for interval in ([endpoint, 2], [0, endpoint]):
                with self.subTest(interval=interval):
                    with self.assertRaises(ValueError):
                        merge_intervals([interval])
        with self.assertRaises(ValueError):
            merge_intervals([[2, 1]])

    def test_input_and_output_do_not_share_mutable_intervals(self):
        ranges = [[8, 10], [1, 2], [3, 5], [9, 9]]
        original = copy.deepcopy(ranges)
        result = merge_intervals(ranges)
        self.assertEqual(result, [[1, 5], [8, 10]])
        self.assertEqual(ranges, original)
        result[0][0] = -100
        result[1][1] = 100
        self.assertEqual(ranges, original)

    def test_30000_intervals(self):
        expected = [[3 * i, 3 * i] for i in range(30000)]
        self.assertEqual(merge_intervals(expected[::-1]), expected)
        adjacent = [(i, i) for i in range(29999, -1, -1)]
        self.assertEqual(merge_intervals(adjacent), [[0, 29999]])

    def test_randomized_integer_coverage(self):
        rng = random.Random(1234)
        for case in range(100):
            ranges = [
                sorted((rng.randint(-20, 20), rng.randint(-20, 20)))
                for _ in range(rng.randrange(20))
            ]
            with self.subTest(case=case):
                result = merge_intervals(ranges)
                expected_points = {
                    point for start, end in ranges for point in range(start, end + 1)
                }
                actual_points = {
                    point for start, end in result for point in range(start, end + 1)
                }
                self.assertEqual(actual_points, expected_points)
                self.assertIsInstance(result, list)
                for interval in result:
                    self.assertIsInstance(interval, list)
                    self.assertEqual(len(interval), 2)
                    self.assertLessEqual(interval[0], interval[1])
                for previous, following in zip(result, result[1:]):
                    self.assertGreater(following[0], previous[1] + 1)
