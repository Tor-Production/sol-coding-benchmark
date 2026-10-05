import unittest

from intervals import merge_intervals


class ContractEdgeTests(unittest.TestCase):
    def test_malformed_inputs(self):
        malformed = (
            None,
            1,
            "1,2",
            [[1]],
            [[1, 2, 3]],
            [[True, 2]],
            [[1, False]],
            [[1.0, 2]],
            [[1, "2"]],
            [[2, 1]],
        )
        for value in malformed:
            with self.subTest(value=value), self.assertRaises(ValueError):
                merge_intervals(value)

    def test_large_values_and_input_unchanged(self):
        huge = 10**100
        source = ([huge, huge], [-3, -2], (-1, 1), [huge - 1, huge - 1])
        self.assertEqual(merge_intervals(source), [[-3, 1], [huge - 1, huge]])
        self.assertEqual(source, ([huge, huge], [-3, -2], (-1, 1), [huge - 1, huge - 1]))

    def test_thirty_thousand_intervals(self):
        intervals = [[n * 2, n * 2] for n in range(29999, -1, -1)]
        result = merge_intervals(intervals)
        self.assertEqual(len(result), 30000)
        self.assertEqual(result[0], [0, 0])
        self.assertEqual(result[-1], [59998, 59998])


if __name__ == "__main__":
    unittest.main()
