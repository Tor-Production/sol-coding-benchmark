import unittest

from intervals import merge_intervals


class ContractTests(unittest.TestCase):
    def test_malformed_inputs(self):
        invalid = (
            None,
            "1, 2",
            {1: 2},
            [[1]],
            [(1, 2, 3)],
            [[True, 2]],
            [[1, False]],
            [[1, 2.0]],
            [[1, "2"]],
            [[3, 1]],
        )
        for ranges in invalid:
            with self.subTest(ranges=ranges), self.assertRaises(ValueError):
                merge_intervals(ranges)

    def test_unsorted_nested_and_large_intervals(self):
        huge = 10**100
        ranges = ([4, 5], (-4, -2), [huge, huge], [2, 4], [-1, 0], [3, 3])
        self.assertEqual(
            merge_intervals(ranges),
            [[-4, 0], [2, 5], [huge, huge]],
        )
        self.assertEqual(ranges[0], [4, 5])
        self.assertEqual(ranges[2], [huge, huge])

    def test_many_intervals(self):
        ranges = [(i, i) for i in range(29_999, -1, -1)]
        self.assertEqual(merge_intervals(ranges), [[0, 29_999]])
        self.assertEqual(ranges[0], (29_999, 29_999))


if __name__ == "__main__":
    unittest.main()
