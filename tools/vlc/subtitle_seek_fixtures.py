"""Mutate only optional Cues in the digest-verified first-party Matroska fixture."""


def _number(data, offset, identifier=False):
    if offset >= len(data) or not data[offset]:
        raise ValueError("Invalid authored EBML number")
    width = 8 - data[offset].bit_length() + 1
    if offset + width > len(data):
        raise ValueError("Truncated authored EBML number")
    value = int.from_bytes(data[offset:offset + width], "big")
    return (value if identifier else value & ((1 << (7 * width)) - 1)), width


def _elements(data, start, end):
    while start < end:
        tag, width = _number(data, start, True)
        length, size = _number(data, start + width)
        body = start + width + size
        stop = body + length
        if stop > end:
            raise ValueError("Authored EBML element exceeds parent")
        yield tag, start, body, stop
        start = stop


def cases(original):
    segment = next(e for e in _elements(original, 0, len(original)) if e[0] == 0x18538067)
    cues = next(e for e in _elements(original, segment[2], segment[3]) if e[0] == 0x1c53bb6b)
    indexed = []
    for point in _elements(original, cues[2], cues[3]):
        if point[0] != 0xbb:
            continue
        for part in _elements(original, point[2], point[3]):
            if part[0] != 0xb7:
                continue
            fields = {e[0]: e for e in _elements(original, part[2], part[3])}
            track = fields[0xf7]
            if int.from_bytes(original[track[2]:track[3]], "big") == 6:
                indexed.append((point, fields))
    if len(indexed) != 1:
        raise ValueError("Review authored ASS cue layout")
    point, fields = indexed[0]
    result = {"normal": (original, "normal")}
    for name, tag, value in [("expired-duration", 0xb2, 1), ("wrong-track", 0xf7, 1),
                             ("bad-offset", 0xf0, 0xffffff), ("bad-cluster", 0xf1, 0xffffff)]:
        field = fields[tag]
        data = bytearray(original)
        width = field[3] - field[2]
        data[field[2]:field[3]] = min(value, (1 << (8 * width)) - 1).to_bytes(width, "big")
        result[name] = (bytes(data), "damaged")
    _, start, body_start, end = cues
    width = body_start - start - 4
    body = original[body_start:end] + original[point[1]:point[3]]
    size = ((1 << (7 * width)) | len(body)).to_bytes(width, "big")
    result["duplicate"] = (original[:start + 4] + size + body + original[end:], "normal")
    return result
