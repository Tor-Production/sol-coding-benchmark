import unittest

from intervals import merge_intervals


class ContractTests(unittest.TestCase):
    def test_merges_duplicates_containment_and_negative_adjacency(self):
        ranges = [[-10, -8], [-7, -5], [-9, -6], [-10, -8], [2, 2]]
        self.assertEqual(merge_intervals(ranges), [[-10, -5], [2, 2]])

    def test_accepts_tuples_and_arbitrarily_large_integers(self):
        large = 10**100
        self.assertEqual(
            merge_intervals(((-large, -large), (-large + 1, 0), (large, large))),
            [[-large, 0], [large, large]],
        )

    def test_does_not_mutate_input(self):
        ranges = [[5, 7], [1, 2], [3, 4]]
        original = [interval[:] for interval in ranges]
        merge_intervals(ranges)
        self.assertEqual(ranges, original)

    def test_rejects_invalid_outer_containers(self):
        for ranges in (None, "", {}, set(), iter(())):
            with self.subTest(ranges=ranges), self.assertRaises(ValueError):
                merge_intervals(ranges)

    def test_rejects_invalid_intervals_and_endpoints(self):
        invalid = (
            [None],
            [[1]],
            [[1, 2, 3]],
            [[True, 2]],
            [[1, False]],
            [[1.0, 2]],
            [[1, "2"]],
            [[2, 1]],
        )
        for ranges in invalid:
            with self.subTest(ranges=ranges), self.assertRaises(ValueError):
                merge_intervals(ranges)

    def test_handles_thirty_thousand_intervals(self):
        ranges = [[2 * i, 2 * i] for i in range(29_999, -1, -1)]
        result = merge_intervals(ranges)
        self.assertEqual(len(result), 30_000)
        self.assertEqual(result[0], [0, 0])
        self.assertEqual(result[-1], [59_998, 59_998])


if __name__ == "__main__":
    unittest.main()
