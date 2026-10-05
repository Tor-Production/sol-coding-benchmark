import unittest

from intervals import merge_intervals


class AdditionalTests(unittest.TestCase):
    def test_validation(self):
        malformed = (
            None,
            {},
            [[1]],
            [[1, 2, 3]],
            [[True, 2]],
            [[1, False]],
            [[1.0, 2]],
            [[2, 1]],
        )
        for ranges in malformed:
            with self.subTest(ranges=ranges), self.assertRaises(ValueError):
                merge_intervals(ranges)

    def test_large_values_and_no_mutation(self):
        huge = 10**100
        ranges = [[5, 5], [-2, -1], [0, 4], [huge, huge]]
        original = [interval[:] for interval in ranges]

        self.assertEqual(merge_intervals(ranges), [[-2, 5], [huge, huge]])
        self.assertEqual(ranges, original)

    def test_thirty_thousand_adjacent_intervals(self):
        ranges = [[value, value] for value in range(30_000)]
        self.assertEqual(merge_intervals(ranges), [[0, 29_999]])


if __name__ == "__main__":
    unittest.main()
