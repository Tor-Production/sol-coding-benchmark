import copy
import unittest

from intervals import merge_intervals


class AdditionalTests(unittest.TestCase):
    def test_invalid_inputs(self):
        invalid = [
            None, 1, "12", {}, set(), iter([]),
            [None], [1], ["12"], [{}], [[]], [[1]], [[1, 2, 3]],
            [[True, 2]], [[0, False]], [[1.0, 2]], [[0, "2"]],
            [[2, 1]], [[0, 1], [3, None]],
        ]
        for ranges in invalid:
            with self.subTest(ranges=ranges):
                with self.assertRaises(ValueError):
                    merge_intervals(ranges)

    def test_union_and_input_preservation(self):
        ranges = [[8, 10], [-5, -3], [-2, 0], [1, 5], [2, 3],
                  [1, 5], [7, 7], [6, 6], [20, 20]]
        original = copy.deepcopy(ranges)
        result = merge_intervals(ranges)
        self.assertEqual(result, [[-5, 10], [20, 20]])
        self.assertEqual(ranges, original)
        result[0][0] = 100
        self.assertEqual(ranges, original)

    def test_tuples_and_large_integers(self):
        large = 10 ** 100
        self.assertEqual(
            merge_intervals(((large + 1, large + 5), [large, large],
                             (-large, -large))),
            [[-large, -large], [large, large + 5]],
        )
        self.assertEqual(merge_intervals(()), [])

    def test_thirty_thousand_intervals(self):
        ranges = [(i, i) for i in reversed(range(30000))]
        self.assertEqual(merge_intervals(ranges), [[0, 29999]])
        separated = [(3 * i, 3 * i) for i in reversed(range(30000))]
        self.assertEqual(merge_intervals(separated),
                         [[3 * i, 3 * i] for i in range(30000)])
