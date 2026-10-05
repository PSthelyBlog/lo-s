#!/usr/bin/env python3
"""Print a GGUF file's architecture, expert counts and how its weights divide between routed
experts and everything else. Reads only the header, so it also works on a partial download.
Usage: gguf-info.py FILE.gguf"""
import struct
import sys

SCALARS = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}
# Bits per weight for the tensor types that occur in common quantizations (approximate for K-quants).
BITS = {0: 32, 1: 16, 2: 4.5, 3: 5, 6: 5, 7: 5.5, 8: 8.5, 10: 2.6, 11: 3.4, 12: 4.5, 13: 5.5, 14: 6.6,
        15: 8, 16: 2.1, 17: 2.3, 18: 3.1, 19: 1.6, 20: 4.25, 21: 3.4, 22: 2.5, 23: 4.5, 30: 16}


class Reader:
    def __init__(self, f):
        self.f = f

    def unpack(self, fmt):
        return struct.unpack("<" + fmt, self.f.read(struct.calcsize(fmt)))[0]

    def string(self):
        return self.f.read(self.unpack("Q")).decode("utf-8", "replace")

    def value(self, kind):
        if kind in SCALARS:
            return self.unpack(SCALARS[kind])
        if kind == 8:
            return self.string()
        if kind == 9:
            item, count = self.unpack("I"), self.unpack("Q")
            values = [self.value(item) for _ in range(count)]
            return values if count <= 8 else f"<{count} items>"
        raise ValueError(f"unknown value type {kind}")


def main():
    r = Reader(open(sys.argv[1], "rb"))
    if r.f.read(4) != b"GGUF":
        sys.exit("not a GGUF file")
    r.unpack("I")
    tensor_count, kv_count = r.unpack("Q"), r.unpack("Q")
    meta = {}
    for _ in range(kv_count):
        key = r.string()
        meta[key] = r.value(r.unpack("I"))
    for key, value in meta.items():
        if key.startswith("general.") and key.split(".")[1] in ("architecture", "name", "size_label", "file_type") \
                or any(word in key for word in ("expert", "block_count", "context_length", "embedding_length")):
            print(f"{key} = {value}")

    groups = {}
    for _ in range(tensor_count):
        name = r.string()
        dims = [r.unpack("Q") for _ in range(r.unpack("I"))]
        kind = r.unpack("I")
        r.unpack("Q")
        count = 1
        for d in dims:
            count *= d
        group = "routed experts" if "_exps." in name else "everything else"
        entry = groups.setdefault(group, [0, 0, 0.0])
        entry[0] += 1
        entry[1] += count
        entry[2] += count * BITS.get(kind, 8) / 8
    print(f"tensors = {tensor_count}")
    for group, (tensors, weights, size) in groups.items():
        print(f"{group}: {tensors} tensors, {weights / 1e9:.2f}B weights, about {size / 1e9:.1f} GB")


if __name__ == "__main__":
    main()
