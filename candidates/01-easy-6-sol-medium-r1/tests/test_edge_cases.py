import unittest

from intervals import merge_intervals


class EdgeCaseTests(unittest.TestCase):
    def test_validation(self):
        invalid = [None, "", {}, [1, 2], [[True, 2]], [[1, False]],
                   [[1]], [[1, 2, 3]], [[2, 1]], [[1.0, 2]]]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                merge_intervals(value)

    def test_large_values_and_input_unchanged(self):
        big = 10 ** 100
        ranges = ([big + 1, big + 2], [-big, -big], [big, big])
        self.assertEqual(merge_intervals(ranges), [[-big, -big], [big, big + 2]])
        self.assertEqual(ranges, ([big + 1, big + 2], [-big, -big], [big, big]))

    def test_many_intervals(self):
        ranges = [[i, i] for i in range(29999, -1, -1)]
        self.assertEqual(merge_intervals(ranges), [[0, 29999]])


if __name__ == "__main__":
    unittest.main()
