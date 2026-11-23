#!/usr/bin/env python3
"""After a desync: show which instances differ between two dronline_state.txt files (written with debug=1).
Usage: python3 tools/diff_state.py <host dronline_state.txt> <client dronline_state.txt>
Prints the first checkpoint where the instance lists differ and the lines only one side has.
Exits with 1 on a difference, and also when one file has checkpoints the other lacks."""
import sys
from collections import Counter


def blocks(path):
    out, cur = {}, None
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.rstrip("\r\n")
        if line.startswith("== frame "):
            cur = line
            out[cur] = []
        elif cur:
            out[cur].append(line)
    return out


a, b = blocks(sys.argv[1]), blocks(sys.argv[2])
for head in a:
    if head not in b:
        print("only in first file:", head)
        sys.exit(1)
    ca, cb = Counter(a[head]), Counter(b[head])
    if ca != cb:
        print("first difference at", head)
        for line, n in sorted((ca - cb).items()):
            print("  first only  ", n, "x", line)
        for line, n in sorted((cb - ca).items()):
            print("  second only ", n, "x", line)
        sys.exit(1)
extra = [head for head in b if head not in a]
if extra:
    print(f"identical in the {len(a)} checkpoints of the first file, but the second goes on with {len(extra)} more,"
          f" from {extra[0]}")
    sys.exit(1)
print(f"identical in {len(a)} checkpoints")
