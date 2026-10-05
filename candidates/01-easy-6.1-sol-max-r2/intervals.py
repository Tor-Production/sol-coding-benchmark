def merge_intervals(ranges):
    """Return the union of inclusive, adjacent integer intervals.

    Raise ValueError for malformed input. The input is never mutated.
    Runtime is O(n log n), with O(n) additional space.
    """
    if not isinstance(ranges, (list, tuple)):
        raise ValueError("ranges must be a list or tuple")

    intervals = []
    for interval in ranges:
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            raise ValueError("each interval must be a two-element list or tuple")
        start, end = interval
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
        ):
            raise ValueError("endpoints must be integers, excluding booleans")
        if start > end:
            raise ValueError("interval start must not exceed end")
        intervals.append((start, end))

    intervals.sort()
    merged = []
    for start, end in intervals:
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        elif end > merged[-1][1]:
            merged[-1][1] = end
    return merged
