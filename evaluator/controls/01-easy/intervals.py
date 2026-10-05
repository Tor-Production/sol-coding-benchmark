def merge_intervals(ranges):
    if not isinstance(ranges, (list, tuple)):
        raise ValueError("Expected a list or tuple")
    work = []
    for pair in ranges:
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or any(type(x) is not int for x in pair) or pair[0] > pair[1]):
            raise ValueError("Invalid interval")
        work.append(list(pair))
    work.sort()
    result = []
    for lo, hi in work:
        if result and lo <= result[-1][1] + 1:
            result[-1][1] = max(result[-1][1], hi)
        else:
            result.append([lo, hi])
    return result
