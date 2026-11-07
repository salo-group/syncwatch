"""Author seek and compressed-cue cases from the verified first-party Matroska fixture."""

import zlib


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


def _element(tag, body):
    width = next(n for n in range(1, 9) if len(body) < (1 << (7 * n)) - 1)
    size = ((1 << (7 * width)) | len(body)).to_bytes(width, "big")
    return tag.to_bytes((tag.bit_length() + 7) // 8, "big") + size + body


def _unsigned(tag, value, width=None):
    return _element(tag, value.to_bytes(width or max(1, (value.bit_length() + 7) // 8), "big"))


def compressed_cues(original, packet_bytes, count=1, *, laces=1, additional_bytes=0, compressed=True):
    """Keep authored A/V blocks; rebuild exact seek offsets around compressed ASS cues."""
    root = list(_elements(original, 0, len(original)))
    segment = next(e for e in root if e[0] == 0x18538067)
    children = list(_elements(original, segment[2], segment[3]))
    info = next(e for e in children if e[0] == 0x1549a966)
    tracks = next(e for e in children if e[0] == 0x1654ae6b)
    entries = []
    for entry in _elements(original, tracks[2], tracks[3]):
        if entry[0] != 0xae:
            continue
        fields = {e[0]: e for e in _elements(original, entry[2], entry[3])}
        number = fields[0xd7]
        body = original[entry[2]:entry[3]]
        if int.from_bytes(original[number[2]:number[3]], "big") == 6 and compressed:
            compression = _element(0x5034, _unsigned(0x4254, 0))
            body += _element(0x6d80, _element(0x6240, _unsigned(0x5032, 1) + compression))
        if int.from_bytes(original[number[2]:number[3]], "big") == 6 and additional_bytes:
            body += _unsigned(0x55ee, 1)
        entries.append(_element(entry[0], body))
    prefix = original[info[1]:info[3]] + _element(0x1654ae6b, b"".join(entries))
    clusters = []
    for cluster in (e for e in children if e[0] == 0x1f43b675):
        parts = []
        timestamp = None
        for part in _elements(original, cluster[2], cluster[3]):
            if part[0] == 0xbf:
                continue
            if part[0] == 0xe7:
                timestamp = int.from_bytes(original[part[2]:part[3]], "big")
            block = None
            if part[0] == 0xa3:
                block = part
            elif part[0] == 0xa0:
                block = next((b for b in _elements(original, part[2], part[3]) if b[0] == 0xa1), None)
            if block and _number(original, block[2])[0] == 6:
                continue
            parts.append(original[part[1]:part[3]])
        if timestamp is None:
            raise ValueError("Authored cluster has no timestamp")
        clusters.append((timestamp, _element(0x1f43b675, b"".join(parts))))
    text = b"0,0,Default,,0,0,0,," + b"A" * packet_bytes
    payload = zlib.compress(text) if compressed else text
    framed = b"\x86\0\0\x04" + bytes([laces - 1]) + payload * laces if laces > 1 else b"\x86\0\0\0" + payload
    block = _element(0xa1, framed)
    additions = _element(0x75a1, _element(0xa6,
        _unsigned(0xee, 1) + _element(0xa5, b"S" * additional_bytes))) if additional_bytes else b""
    group = _element(0xa0, block + _unsigned(0x9b, 7900) + additions)
    stamp = _unsigned(0xe7, 0)
    cue_cluster = _element(0x1f43b675, stamp + group * count)
    clusters.insert(0, (0, cue_cluster))

    def seek_head(position):
        return _element(0x114d9b74, _element(0x4dbb,
            _element(0x53ab, bytes.fromhex("1c53bb6b")) + _unsigned(0x53ac, position, 8)))

    position = len(seek_head(0)) + len(prefix)
    cues = []
    for index, (timestamp, cluster) in enumerate(clusters):
        if index == 0:
            for ordinal in range(count):
                fields = _unsigned(0xf7, 6) + _unsigned(0xf1, position)
                fields += _unsigned(0xf0, len(stamp) + ordinal * len(group)) + _unsigned(0xb2, 7900)
                cues.append(_element(0xbb, _unsigned(0xb3, 0) + _element(0xb7, fields)))
        else:
            fields = _unsigned(0xf7, 1) + _unsigned(0xf1, position)
            cues.append(_element(0xbb, _unsigned(0xb3, timestamp) + _element(0xb7, fields)))
        position += len(cluster)
    body = seek_head(position) + prefix + b"".join(c for _, c in clusters) + _element(0x1c53bb6b, b"".join(cues))
    header = b"".join(original[e[1]:e[3]] for e in root if e[0] != 0x18538067)
    return header + _element(0x18538067, body)


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
    for name, size, count, options in [
            ("small", 65536, 1, {}), ("oversized", 5 * 1024 * 1024, 1, {}),
            ("aggregate", 1536 * 1024, 4, {}),
            ("small-laced", 64, 1, {"laces": 4, "additional_bytes": 1024}),
            ("small-raw-laced", 64, 1, {"laces": 4, "additional_bytes": 1024, "compressed": False}),
            ("laced-side-data", 64, 1, {"laces": 256, "additional_bytes": 240 * 1024}),
            ("raw-laced-side-data", 64, 1, {"laces": 256, "additional_bytes": 220 * 1024, "compressed": False})]:
        name = "bounded-" + name
        result[name] = (compressed_cues(original, size, count, **options), name)
    return result
