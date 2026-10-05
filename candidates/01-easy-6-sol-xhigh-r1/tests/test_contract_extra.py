import unittest

from intervals import merge_intervals


class ContractTests(unittest.TestCase):
    def test_malformed_inputs(self):
        for value in (
            None, {}, 3, "intervals", [1], [[1]], [[1, 2, 3]],
            [[True, 2]], [(0, False)], [[1.0, 2]], [[2, 1]],
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                merge_intervals(value)

    def test_large_and_negative_endpoints(self):
        large = 10 ** 100
        self.assertEqual(
            merge_intervals(((large + 1, large + 1), (-2, -1), (large, large))),
            [[-2, -1], [large, large + 1]],
        )

    def test_large_input_is_not_mutated(self):
        source = [[i, i] for i in range(30_000, 0, -1)]
        original = [item[:] for item in source]
        self.assertEqual(merge_intervals(source), [[1, 30_000]])
        self.assertEqual(source, original)


if __name__ == "__main__":
    unittest.main()
