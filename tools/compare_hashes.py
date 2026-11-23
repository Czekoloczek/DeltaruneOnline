#!/usr/bin/env python3
"""Compare the state logs of a record run and a replay run.
Usage: python3 compare_hashes.py dronline_hash_record.txt dronline_hash_replay.txt
Each line: frame room instance_count sum_x sum_y plot. Prints the first frame where they differ."""
import sys

a = [l.split() for l in open(sys.argv[1]) if l.strip()]
b = [l.split() for l in open(sys.argv[2]) if l.strip()]
for x, y in zip(a, b):
    if x != y:
        print("DESYNC at frame", x[0])
        print("  record:", " ".join(x))
        print("  replay:", " ".join(y))
        sys.exit(1)
print(f"in sync for {min(len(a), len(b))} checkpoints (record {len(a)}, replay {len(b)})")
