import unittest

from intervals import merge_intervals


class AdditionalTests(unittest.TestCase):
    def test_rejects_malformed_input(self):
        malformed = [
            None,
            "not a container",
            iter(()),
            [[1]],
            [[1, 2, 3]],
            [[True, 2]],
            [[1, False]],
            [[1.0, 2]],
            [["1", 2]],
            [[2, 1]],
            [None],
        ]
        for value in malformed:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    merge_intervals(value)

    def test_does_not_mutate_and_handles_large_integers(self):
        huge = 10**100
        ranges = [[5, 5], [-2, 1], [2, 4], [huge, huge]]
        original = [interval[:] for interval in ranges]

        self.assertEqual(merge_intervals(ranges), [[-2, 5], [huge, huge]])
        self.assertEqual(ranges, original)

    def test_thirty_thousand_intervals(self):
        ranges = [[value, value] for value in range(29_999, -1, -1)]
        self.assertEqual(merge_intervals(ranges), [[0, 29_999]])


if __name__ == "__main__":
    unittest.main()
