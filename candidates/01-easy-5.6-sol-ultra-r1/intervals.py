def merge_intervals(ranges):
    """Return the union of inclusive, adjacent integer intervals."""
    if not isinstance(ranges, (list, tuple)):
        raise ValueError("ranges must be a list or tuple")

    validated = []
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
            raise ValueError("interval endpoints must be integers")
        if start > end:
            raise ValueError("interval start must not exceed its end")

        validated.append((start, end))

    validated.sort(key=lambda interval: interval[0])

    merged = []
    for start, end in validated:
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        elif end > merged[-1][1]:
            merged[-1][1] = end

    return merged
