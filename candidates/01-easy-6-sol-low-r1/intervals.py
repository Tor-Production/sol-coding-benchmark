def merge_intervals(ranges):
    """Return the union of inclusive, adjacent integer intervals."""
    if not isinstance(ranges, (list, tuple)):
        raise ValueError("ranges must be a list or tuple")

    intervals = []
    for interval in ranges:
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            raise ValueError("each interval must have two endpoints")
        start, end = interval
        if (type(start) is not int or type(end) is not int
                or start > end):
            raise ValueError("interval endpoints must be ordered integers")
        intervals.append((start, end))

    intervals.sort()
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1] + 1:
            if end > merged[-1][1]:
                merged[-1][1] = end
        else:
            merged.append([start, end])
    return merged
