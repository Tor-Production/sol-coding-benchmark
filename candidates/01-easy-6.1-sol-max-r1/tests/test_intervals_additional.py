import copy
import random
import unittest

from intervals import merge_intervals


class AdditionalIntervalTests(unittest.TestCase):
    def test_tuple_input_and_list_output(self):
        self.assertEqual(merge_intervals(()), [])
        result = merge_intervals(((8, 9), [1, 2], (4, 6)))
        self.assertEqual(result, [[1, 2], [4, 6], [8, 9]])
        self.assertIsInstance(result, list)
        self.assertTrue(all(isinstance(interval, list) for interval in result))

    def test_containment_duplicates_and_adjacency_chain(self):
        ranges = [[7, 9], [2, 3], [1, 10], [1, 10], [11, 11], [12, 15]]
        self.assertEqual(merge_intervals(ranges), [[1, 15]])

    def test_negative_and_arbitrarily_large_integers(self):
        huge = 10 ** 1000
        ranges = [
            [huge + 1, huge + 5],
            [-huge, -huge + 2],
            [huge, huge],
            [-huge + 3, -huge + 4],
            [-5, -1],
            [0, 0],
            [2, 3],
        ]
        self.assertEqual(
            merge_intervals(ranges),
            [[-huge, -huge + 4], [-5, 0], [2, 3], [huge, huge + 5]],
        )

    def test_input_is_preserved_and_output_is_independent(self):
        ranges = [[8, 10], [1, 2], [3, 4], [1, 1]]
        original = copy.deepcopy(ranges)
        result = merge_intervals(ranges)
        self.assertEqual(ranges, original)
        self.assertEqual(result, [[1, 4], [8, 10]])
        result[0][0] = -100
        result[1][1] = 100
        self.assertEqual(ranges, original)

    def test_invalid_outer_containers(self):
        invalid = [None, False, 1, 1.5, "", {}, {1, 2}, iter([[1, 2]])]
        for ranges in invalid:
            with self.subTest(container=type(ranges).__name__):
                with self.assertRaises(ValueError):
                    merge_intervals(ranges)

    def test_invalid_interval_containers_and_lengths(self):
        invalid = [None, False, 1, "12", {1: 2}, {1, 2}, iter([1, 2]), [], [1], (1, 2, 3)]
        for interval in invalid:
            with self.subTest(interval=interval):
                ranges = [[0, 0], interval]
                with self.assertRaises(ValueError):
                    merge_intervals(ranges)
                self.assertEqual(ranges[0], [0, 0])

    def test_invalid_endpoints_and_reversed_intervals(self):
        invalid = [True, False, 1.0, "1", None, 1 + 0j, [], {}]
        for endpoint in invalid:
            for interval in ([endpoint, 2], [0, endpoint]):
                with self.subTest(interval=interval):
                    with self.assertRaises(ValueError):
                        merge_intervals([interval])
        for interval in ([2, 1], [0, -1], (-1, -2)):
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    merge_intervals([interval])

    def test_thirty_thousand_intervals(self):
        count = 30000
        separated = [[3 * index, 3 * index] for index in reversed(range(count))]
        expected = [[3 * index, 3 * index] for index in range(count)]
        self.assertEqual(merge_intervals(separated), expected)
        adjacent = [(index, index) for index in reversed(range(count))]
        self.assertEqual(merge_intervals(adjacent), [[0, count - 1]])

    def test_randomized_union_against_integer_set(self):
        rng = random.Random(12345)
        for case in range(100):
            ranges = []
            covered = set()
            for _ in range(rng.randrange(30)):
                start, end = sorted((rng.randint(-20, 20), rng.randint(-20, 20)))
                ranges.append([start, end])
                covered.update(range(start, end + 1))
            result = merge_intervals(ranges)
            with self.subTest(case=case):
                actual = {value for start, end in result for value in range(start, end + 1)}
                self.assertEqual(actual, covered)
                self.assertTrue(all(start <= end for start, end in result))
                self.assertTrue(
                    all(left[1] + 1 < right[0] for left, right in zip(result, result[1:]))
                )


if __name__ == "__main__":
    unittest.main()
