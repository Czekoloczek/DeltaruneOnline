#!/usr/bin/env python3
"""Compare the state logs of a record run and a replay run.
Usage: python3 compare_hashes.py dronline_hash_record.txt dronline_hash_replay.txt
Each line: frame room instance_count sum_x sum_y plot. Prints the first frame where they differ.
Exits with 1 on a difference, and also when the replay log is shorter: a replay that stopped early (or lost its
log) is not in sync. A longer replay is fine: after the recorded input ends, the replay goes on without input."""
import sys

a = [l.split() for l in open(sys.argv[1]) if l.strip()]
b = [l.split() for l in open(sys.argv[2]) if l.strip()]
if not a:
    sys.exit("the record log is empty: nothing to compare")
for x, y in zip(a, b):
    if x != y:
        print("DESYNC at frame", x[0])
        print("  record:", " ".join(x))
        print("  replay:", " ".join(y))
        sys.exit(1)
if len(b) < len(a):
    print(f"the replay log stops after {len(b)} of {len(a)} checkpoints (next in the record log: frame {a[len(b)][0]})")
    sys.exit(1)
print(f"in sync for {len(a)} checkpoints (record {len(a)}, replay {len(b)})")
