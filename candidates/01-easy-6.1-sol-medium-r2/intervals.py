def merge_intervals(ranges):
    """Return merged inclusive integer intervals without modifying the input.

    Invalid containers, endpoints, or reversed intervals raise ValueError.
    Sorting takes O(n log n) time; the copied intervals use O(n) space.
    """
    if not isinstance(ranges, (list, tuple)):
        raise ValueError("ranges must be a list or tuple")

    ordered = []
    for interval in ranges:
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            raise ValueError("each interval must be a two-element list or tuple")
        start, end = interval
        if (not isinstance(start, int) or isinstance(start, bool)
                or not isinstance(end, int) or isinstance(end, bool)):
            raise ValueError("endpoints must be integers, excluding booleans")
        if start > end:
            raise ValueError("interval start must not exceed its end")
        ordered.append((start, end))

    ordered.sort()
    merged = []
    for start, end in ordered:
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged
