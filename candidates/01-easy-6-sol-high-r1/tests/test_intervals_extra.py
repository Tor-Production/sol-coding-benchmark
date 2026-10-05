import unittest

from intervals import merge_intervals


class IntervalContractTests(unittest.TestCase):
    def test_containment_duplicates_and_large_negative_endpoints(self):
        big = 10**100
        self.assertEqual(
            merge_intervals(((big, big), [-big, -big + 2], [-big + 1, -big + 1], (big, big))),
            [[-big, -big + 2], [big, big]],
        )

    def test_input_is_not_mutated(self):
        ranges = [[4, 5], [1, 2], [2, 3]]
        self.assertEqual(merge_intervals(ranges), [[1, 5]])
        self.assertEqual(ranges, [[4, 5], [1, 2], [2, 3]])

    def test_malformed_input(self):
        invalid = (
            None,
            ((1, 2) for _ in range(1)),
            [1, 2],
            [[1]],
            [[1, 2, 3]],
            [[True, 2]],
            [[1, False]],
            [[1.0, 2]],
            [[2, 1]],
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                merge_intervals(value)

    def test_many_intervals(self):
        ranges = [[i, i] for i in range(29999, -1, -1)]
        self.assertEqual(merge_intervals(ranges), [[0, 29999]])


if __name__ == "__main__":
    unittest.main()
