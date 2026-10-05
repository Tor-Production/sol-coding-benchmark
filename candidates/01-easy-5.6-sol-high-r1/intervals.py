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

    if not validated:
        return []

    validated.sort()
    merged = []
    current_start, current_end = validated[0]

    for start, end in validated[1:]:
        if start <= current_end + 1:
            if end > current_end:
                current_end = end
        else:
            merged.append([current_start, current_end])
            current_start, current_end = start, end

    merged.append([current_start, current_end])
    return merged
