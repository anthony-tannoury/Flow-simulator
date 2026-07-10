#!/usr/bin/env python3
"""Compare a Python-salabim trace with a salabim++ trace.

Normalizes away the only legitimate differences:
  * source line-number column and '@ <line>+' scheduling references
  * 'line numbers prefixed by X refer to <file>' announcement lines
'create' lines may interleave differently (C++ member init vs Python setup
order), so they are compared as an unordered multiset per (time-block); all
other lines must match in exact order.
"""
import re
import sys
from collections import Counter

ANNOUNCE = re.compile(r"line numbers (prefixed by|refers to)")
HEADER = re.compile(r"^line#|^-{4,}")
SCHEDREF = re.compile(r"@\s*[A-Z]?\d+\+?")     # '@  C97+', '@ 12+'


def normalize(path):
    ordered = []          # order-strict event lines
    creates = Counter()   # order-free 'create' lines
    with open(path) as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            if ANNOUNCE.search(line) or HEADER.match(line):
                continue
            line = SCHEDREF.sub("@LINE", line)
            line = line[6:]  # fixed-width source-line-number column
            # canonicalize number tokens: Python traces float-typed quantities
            # as '3.0' where C++ (double-only) prints '3' — same value
            toks = []
            for t in line.split():
                try:
                    toks.append(f"{float(t):.12g}")
                except ValueError:
                    toks.append(t)
            line = " ".join(toks)
            if not line:
                continue
            if " create" in line:
                creates[line] += 1
            else:
                ordered.append(line)
    return ordered, creates


a_ord, a_cre = normalize(sys.argv[1])
b_ord, b_cre = normalize(sys.argv[2])

ok = True
if a_cre != b_cre:
    ok = False
    print("CREATE-LINE MISMATCH:")
    for k in (a_cre - b_cre):
        print(f"  only in {sys.argv[1]}: {k} (x{(a_cre - b_cre)[k]})")
    for k in (b_cre - a_cre):
        print(f"  only in {sys.argv[2]}: {k} (x{(b_cre - a_cre)[k]})")

n = min(len(a_ord), len(b_ord))
for i in range(n):
    if a_ord[i] != b_ord[i]:
        ok = False
        print(f"FIRST ORDERED DIVERGENCE at event #{i}:")
        for j in range(max(0, i - 3), min(n, i + 4)):
            mark = ">>" if j == i else "  "
            print(f" {mark} py : {a_ord[j]}")
            print(f" {mark} cpp: {b_ord[j]}")
        break
if len(a_ord) != len(b_ord):
    ok = False
    print(f"LENGTH MISMATCH: {len(a_ord)} vs {len(b_ord)} ordered events")
    src = a_ord if len(a_ord) > len(b_ord) else b_ord
    for l in src[n:n + 5]:
        print(f"  extra: {l}")

print(f"{len(a_ord)} ordered events + {sum(a_cre.values())} create lines compared")
print("TRACES EQUIVALENT" if ok else "TRACES DIFFER")
sys.exit(0 if ok else 1)
