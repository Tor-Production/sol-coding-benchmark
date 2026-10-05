import copy
import unittest

from intervals import merge_intervals


class AdditionalTests(unittest.TestCase):
    def test_overlap_containment_duplicates_and_negative_values(self):
        ranges = [[8, 10], [-4, -2], [2, 5], [1, 7], [2, 5], [-1, 0]]
        self.assertEqual(merge_intervals(ranges), [[-4, 10]])

    def test_tuple_input_and_large_integers(self):
        large = 10 ** 100
        self.assertEqual(
            merge_intervals(((large + 2, large + 4), [large, large + 1],
                             (-large, -large))),
            [[-large, -large], [large, large + 4]],
        )
        self.assertEqual(merge_intervals(()), [])

    def test_invalid_inputs(self):
        invalid = [None, 1, "12", {}, {1, 2}, iter([]),
                   [None], [1], ["12"], [{}], [[]], [[1]], [[1, 2, 3]],
                   [[True, 2]], [[0, False]], [[1.0, 2]], [[1, 2.0]],
                   [["1", 2]], [[1, None]], [[3, 1]]]
        for ranges in invalid:
            with self.subTest(ranges=ranges):
                with self.assertRaises(ValueError):
                    merge_intervals(ranges)

    def test_input_and_output_are_independent(self):
        ranges = [[8, 9], [1, 3], [2, 4]]
        original = copy.deepcopy(ranges)
        result = merge_intervals(ranges)
        self.assertEqual(ranges, original)
        result[0][0] = -100
        self.assertEqual(ranges, original)

    def test_thirty_thousand_intervals(self):
        self.assertEqual(
            merge_intervals([(i, i) for i in reversed(range(30000))]),
            [[0, 29999]],
        )
        ranges = [(i * 3, i * 3) for i in reversed(range(30000))]
        self.assertEqual(merge_intervals(ranges),
                         [[i * 3, i * 3] for i in range(30000)])
