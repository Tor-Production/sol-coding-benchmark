import unittest

from intervals import merge_intervals


class EdgeCaseTests(unittest.TestCase):
    def test_invalid_inputs(self):
        invalid = (
            None, 1, "1,2", {1: 2},
            [None], [[1]], [[1, 2, 3]], [[2, 1]],
            [[True, 2]], [[1, False]], [[1.0, 2]], [[1, "2"]],
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                merge_intervals(value)

    def test_large_integer_and_negative_intervals(self):
        large = 10 ** 100
        ranges = ((large + 1, large + 3), (-5, -4), (large, large))
        self.assertEqual(merge_intervals(ranges), [[-5, -4], [large, large + 3]])

    def test_many_intervals_and_input_preservation(self):
        ranges = [[i, i] for i in range(29999, -1, -1)]
        original = [interval[:] for interval in ranges]
        self.assertEqual(merge_intervals(ranges), [[0, 29999]])
        self.assertEqual(ranges, original)


if __name__ == "__main__":
    unittest.main()
