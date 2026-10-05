import copy
import unittest

from intervals import merge_intervals


class AdditionalTests(unittest.TestCase):
    def test_overlap_containment_duplicates_and_adjacency(self):
        self.assertEqual(
            merge_intervals([[7, 9], [2, 6], [3, 4], [2, 6], [1, 1], [12, 12]]),
            [[1, 9], [12, 12]],
        )

    def test_tuple_input_and_empty_tuple(self):
        self.assertEqual(merge_intervals(((4, 5), [1, 3])), [[1, 5]])
        self.assertEqual(merge_intervals(()), [])

    def test_negative_and_arbitrarily_large_integers(self):
        huge = 10 ** 100
        self.assertEqual(
            merge_intervals([[huge + 1, huge + 3], [-huge, -huge],
                             [-3, -1], [0, 0], [huge, huge]]),
            [[-huge, -huge], [-3, 0], [huge, huge + 3]],
        )

    def test_invalid_outer_containers(self):
        for value in (None, 1, True, "", "12", {}, set(), iter([])):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    merge_intervals(value)

    def test_invalid_intervals(self):
        for interval in (None, 1, "12", {1: 2}, {1, 2}, [], [1],
                         [1, 2, 3], [2, 1], [False, 2], [1, True],
                         [1.0, 2], [1, 2.0], ["1", 2], [1, None]):
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    merge_intervals([[0, 0], interval])

    def test_input_is_unchanged_and_output_is_independent(self):
        ranges = [[5, 8], [1, 4], [2, 3]]
        original = copy.deepcopy(ranges)
        identities = [id(interval) for interval in ranges]
        result = merge_intervals(ranges)
        self.assertEqual(ranges, original)
        self.assertEqual([id(interval) for interval in ranges], identities)
        self.assertEqual(result, [[1, 8]])
        result[0][0] = -100
        self.assertEqual(ranges, original)

    def test_invalid_input_is_unchanged(self):
        ranges = [[5, 6], [1, 2], [4, 3]]
        original = copy.deepcopy(ranges)
        with self.assertRaises(ValueError):
            merge_intervals(ranges)
        self.assertEqual(ranges, original)

    def test_thirty_thousand_intervals(self):
        self.assertEqual(
            merge_intervals([[i, i] for i in reversed(range(30000))]),
            [[0, 29999]],
        )
        ranges = [[3 * i, 3 * i + 1] for i in range(30000)]
        self.assertEqual(merge_intervals(ranges[::-1]), ranges)


if __name__ == "__main__":
    unittest.main()
