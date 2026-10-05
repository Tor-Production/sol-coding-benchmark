import unittest

from intervals import merge_intervals


class EdgeCaseTests(unittest.TestCase):
    def test_negative_large_contained_and_input_unchanged(self):
        huge = 10**200
        ranges = [[5, 5], [-3, -1], [0, 0], [5, 8], [6, 7], [huge, huge]]
        original = [interval[:] for interval in ranges]

        self.assertEqual(
            merge_intervals(ranges),
            [[-3, 0], [5, 8], [huge, huge]],
        )
        self.assertEqual(ranges, original)

    def test_malformed_inputs(self):
        invalid_values = (
            None,
            {1: 2},
            "not a container",
            [1, 2],
            [[1]],
            [[1, 2, 3]],
            [[True, 2]],
            [[1, False]],
            [[1.0, 2]],
            [[2, 1]],
        )

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    merge_intervals(value)

    def test_thirty_thousand_intervals(self):
        ranges = [[value, value] for value in reversed(range(30_000))]
        self.assertEqual(merge_intervals(ranges), [[0, 29_999]])


if __name__ == "__main__":
    unittest.main()
