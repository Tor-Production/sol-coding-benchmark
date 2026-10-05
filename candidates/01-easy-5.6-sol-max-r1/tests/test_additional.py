import unittest

from intervals import merge_intervals


class AdditionalTests(unittest.TestCase):
    def test_tuple_input_negative_and_large_endpoints(self):
        huge = 10**100
        self.assertEqual(
            merge_intervals(((-10, -8), (-7, -7), (huge, huge))),
            [[-10, -7], [huge, huge]],
        )

    def test_input_is_not_mutated(self):
        ranges = [[5, 5], [1, 2], [3, 4]]
        self.assertEqual(merge_intervals(ranges), [[1, 5]])
        self.assertEqual(ranges, [[5, 5], [1, 2], [3, 4]])

    def test_invalid_outer_containers(self):
        for ranges in (None, "", {}, range(2), iter(())):
            with self.subTest(ranges=ranges), self.assertRaises(ValueError):
                merge_intervals(ranges)

    def test_invalid_intervals_and_endpoints(self):
        invalid = (
            [[1]],
            [[1, 2, 3]],
            ["12"],
            [[True, 2]],
            [[1, False]],
            [[1.0, 2]],
            [[1, None]],
            [[2, 1]],
        )
        for ranges in invalid:
            with self.subTest(ranges=ranges), self.assertRaises(ValueError):
                merge_intervals(ranges)

    def test_thirty_thousand_adjacent_intervals(self):
        ranges = [[2 * value, 2 * value + 1] for value in range(30_000)]
        self.assertEqual(merge_intervals(ranges), [[0, 59_999]])


if __name__ == "__main__":
    unittest.main()
