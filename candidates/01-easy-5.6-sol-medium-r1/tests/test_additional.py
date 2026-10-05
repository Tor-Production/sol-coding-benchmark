import unittest

from intervals import merge_intervals


class AdditionalTests(unittest.TestCase):
    def test_contained_duplicates_negative_and_large(self):
        huge = 10**100
        self.assertEqual(
            merge_intervals([[2, 4], [-3, -1], [1, 5], [-1, 0], [1, 5], [huge, huge]]),
            [[-3, 5], [huge, huge]],
        )

    def test_input_is_not_mutated(self):
        ranges = [[5, 6], [1, 2]]
        self.assertEqual(merge_intervals(ranges), [[1, 2], [5, 6]])
        self.assertEqual(ranges, [[5, 6], [1, 2]])

    def test_malformed_inputs(self):
        malformed = (
            None,
            "1,2",
            {"start": 1, "end": 2},
            [[1]],
            [[1, 2, 3]],
            [[True, 2]],
            [[1, False]],
            [[1.0, 2]],
            [[1, "2"]],
            [(2, 1)],
        )
        for value in malformed:
            with self.subTest(value=value), self.assertRaises(ValueError):
                merge_intervals(value)

    def test_thirty_thousand_intervals(self):
        ranges = [[number * 2, number * 2] for number in range(30_000)]
        self.assertEqual(len(merge_intervals(ranges)), 30_000)


if __name__ == "__main__":
    unittest.main()
