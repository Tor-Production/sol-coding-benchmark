import unittest

from intervals import merge_intervals


class AdditionalTests(unittest.TestCase):
    def test_negative_large_contained_and_unchanged_input(self):
        huge = 10**100
        ranges = [[huge + 1, huge + 3], [-5, -3], [huge, huge + 1], [-4, -4]]
        original = [interval[:] for interval in ranges]

        self.assertEqual(merge_intervals(ranges), [[-5, -3], [huge, huge + 3]])
        self.assertEqual(ranges, original)

    def test_malformed_inputs(self):
        invalid = (
            None,
            1,
            "",
            {},
            [1, 2],
            [[1]],
            [[1, 2, 3]],
            [[False, 1]],
            [[1, True]],
            [[1, 1.0]],
            [[2, 1]],
        )
        for ranges in invalid:
            with self.subTest(ranges=ranges), self.assertRaises(ValueError):
                merge_intervals(ranges)

    def test_many_adjacent_intervals(self):
        ranges = [[2 * i, 2 * i + 1] for i in range(29999, -1, -1)]
        self.assertEqual(merge_intervals(ranges), [[0, 59999]])


if __name__ == "__main__":
    unittest.main()
