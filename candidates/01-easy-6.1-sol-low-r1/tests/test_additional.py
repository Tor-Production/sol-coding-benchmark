import copy
import unittest

from intervals import merge_intervals


class AdditionalTests(unittest.TestCase):
    def test_invalid_inputs(self):
        invalid = [None, 1, "", {}, iter([]), [None], [[1]],
                   [[1, 2, 3]], [{1, 2}], [[True, 2]], [[1, False]],
                   [[1.0, 2]], [[1, "2"]], [[2, 1]]]
        for ranges in invalid:
            with self.subTest(ranges=ranges):
                with self.assertRaises(ValueError):
                    merge_intervals(ranges)

    def test_union_and_no_mutation(self):
        ranges = [[8, 9], [-5, -3], [-2, 0], [8, 9], [8, 8], [2, 4]]
        original = copy.deepcopy(ranges)
        result = merge_intervals(ranges)
        self.assertEqual(result, [[-5, 0], [2, 4], [8, 9]])
        self.assertEqual(ranges, original)
        result[0][0] = 100
        self.assertEqual(ranges, original)

    def test_tuple_and_large_integers(self):
        big = 10 ** 100
        self.assertEqual(merge_intervals(((big + 1, big + 2), (big, big))),
                         [[big, big + 2]])
        self.assertEqual(merge_intervals(()), [])

    def test_thirty_thousand_intervals(self):
        ranges = [(i, i) for i in reversed(range(30000))]
        self.assertEqual(merge_intervals(ranges), [[0, 29999]])


if __name__ == "__main__":
    unittest.main()
