import unittest

from intervals import merge_intervals


class ContractTests(unittest.TestCase):
    def test_rejects_malformed_input(self):
        malformed = (
            None,
            42,
            "1, 2",
            {1: 2},
            [None],
            [(1,)],
            [(1, 2, 3)],
            [[1, 2.0]],
            [[True, 2]],
            [[1, False]],
            [[2, 1]],
        )
        for ranges in malformed:
            with self.subTest(ranges=ranges):
                with self.assertRaises(ValueError):
                    merge_intervals(ranges)

    def test_large_integers_and_input_unchanged(self):
        huge = 10**100
        ranges = [
            [5, 5],
            [2, 4],
            [-huge, -huge + 1],
            [huge, huge],
            [3, 3],
            [6, 10],
            [5, 5],
        ]
        original = [interval[:] for interval in ranges]
        self.assertEqual(
            merge_intervals(ranges),
            [[-huge, -huge + 1], [2, 10], [huge, huge]],
        )
        self.assertEqual(ranges, original)

    def test_thirty_thousand_intervals(self):
        ranges = tuple((i, i) for i in range(29_999, -1, -1))
        self.assertEqual(merge_intervals(ranges), [[0, 29_999]])


if __name__ == "__main__":
    unittest.main()
