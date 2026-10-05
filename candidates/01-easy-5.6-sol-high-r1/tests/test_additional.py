import unittest

from intervals import merge_intervals


class AdditionalTests(unittest.TestCase):
    def test_containment_duplicates_negatives_and_large_integers(self):
        huge = 10**100
        self.assertEqual(
            merge_intervals([[1, 5], [2, 3], [1, 5], [-2, 0], [huge, huge]]),
            [[-2, 5], [huge, huge]],
        )

    def test_does_not_mutate_input(self):
        ranges = [[5, 7], [1, 2], [3, 4]]
        original = [interval[:] for interval in ranges]
        merge_intervals(ranges)
        self.assertEqual(ranges, original)

    def test_rejects_malformed_input(self):
        malformed = (
            None,
            "1,2",
            {"start": 1, "end": 2},
            [[1]],
            [[1, 2, 3]],
            [[True, 2]],
            [[1, False]],
            [[1.0, 2]],
            [[2, 1]],
        )
        for value in malformed:
            with self.subTest(value=value), self.assertRaises(ValueError):
                merge_intervals(value)

    def test_thirty_thousand_intervals(self):
        ranges = [[number * 2, number * 2] for number in range(30_000)]
        self.assertEqual(merge_intervals(ranges), ranges)


if __name__ == "__main__":
    unittest.main()
