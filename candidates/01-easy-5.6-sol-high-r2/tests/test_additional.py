import unittest

from intervals import merge_intervals


class AdditionalTests(unittest.TestCase):
    def test_merges_duplicates_containment_and_negative_intervals(self):
        self.assertEqual(
            merge_intervals([[5, 5], [-3, -1], [-1, 4], [5, 5]]),
            [[-3, 5]],
        )

    def test_does_not_mutate_input_and_supports_large_integers(self):
        large = 10**100
        ranges = [[large, large], [2, 3], [1, 1]]
        original = [interval[:] for interval in ranges]
        self.assertEqual(merge_intervals(ranges), [[1, 3], [large, large]])
        self.assertEqual(ranges, original)

    def test_rejects_malformed_input(self):
        invalid_values = [
            None,
            {},
            "1,2",
            1,
            [[1]],
            [[1, 2, 3]],
            [[True, 2]],
            [[1, False]],
            [[1.0, 2]],
            [[2, 1]],
        ]
        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                merge_intervals(value)

    def test_handles_thirty_thousand_intervals(self):
        ranges = [[value, value] for value in range(0, 60_000, 2)]
        self.assertEqual(len(merge_intervals(ranges)), 30_000)


if __name__ == "__main__":
    unittest.main()
