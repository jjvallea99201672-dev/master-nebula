"""
Geometric Hash Anomaly Test — FIXED version (2026-10-23 audit)

What was wrong with the original, and what this version does instead:

1. PREFIX UNITS. The original sliced hex strings (h[:4] = 4 hex chars
   = 2 bytes) but reported the result as "4 bytes". This version
   measures prefixes in BYTES on the raw SHA-256 digest.

2. COLLISION COUNTING. The original used O(n^2) pair loops. This
   version counts with a Counter in O(n) and reports colliding
   *pairs* and *groups*.

3. BASELINE. The original treated "0 collisions" as the pass mark.
   Short prefixes collide by pure chance (birthday paradox), so
   every count is printed next to its birthday expectation.
   ANOMALY = deviation from that baseline, not collisions existing.

4. CONTROL. A same-size set of uniformly random points is hashed
   the exact same way. If lattice coordinates collide more than
   random coordinates do, that is evidence. If both match the
   baseline, the hash space is behaving randomly for both.

5. GEOMETRY PARAMETERS ACTUALLY USED. In the original, delta_rho
   and ideal_step were defined but never used, and the generator
   was a binary tree mislabelled as a 3-coordinated Bethe lattice.
   Here, node depth d sits at hyperbolic radius rho = d * delta,
   mapped into the Poincare disk as r = R_inf * tanh(rho / 2),
   and the user's step (0.59) and the ideal step (ln 2) are run
   side by side. Model named honestly: a binary sector tree in
   the disk (2 children/node), Bethe-like, not a {p,q} tiling.

6. OVERLAP. Same-depth nodes share a radius, so their nearest
   neighbours are adjacent in angle. Sorting per depth replaces
   the O(n^2) scan (and there is no centre-node self-pair).

Coordinates are formatted to 6 decimal places, exactly as in the
original, and duplicate coordinates are counted explicitly.

Usage: python3 geometric_hash_test_fixed.py [--depth 12]
"""
import argparse, hashlib, math, random
from collections import Counter, defaultdict

R_INF = 477.0
USER_STEP = 0.59
IDEAL_STEP = math.log(2)          # ~0.693147
PREFIX_BYTES = (1, 2, 4)          # measured on raw digest bytes

def generate(step, max_depth):
    """Binary sector tree: node at sector centre, hyperbolic radius d*step."""
    nodes = []
    def gen(depth, a0, a1):
        if depth > max_depth:
            return
        mid = (a0 + a1) / 2
        r = R_INF * math.tanh(depth * step / 2) if depth else 0.0
        nodes.append((depth, mid, round(r * math.cos(mid), 6), round(r * math.sin(mid), 6)))
        gen(depth + 1, a0, mid)
        gen(depth + 1, mid, a1)
    gen(0, 0.0, 2 * math.pi)
    return nodes

def random_control(n, seed=99):
    rng = random.Random(seed)
    pts = []
    for _ in range(n):
        a = rng.uniform(0, 2 * math.pi)
        r = R_INF * math.sqrt(rng.random())   # uniform over the disk
        pts.append((round(r * math.cos(a), 6), round(r * math.sin(a), 6)))
    return pts

def geometry_stats(nodes):
    by_depth = defaultdict(list)
    for d, ang, x, y in nodes:
        by_depth[d].append((ang, x, y))
    min_sep, overlap_pairs = math.inf, 0
    for d, group in by_depth.items():
        if len(group) < 2:
            continue
        group.sort()
        m = len(group)
        for i in range(m):
            _, x1, y1 = group[i]
            _, x2, y2 = group[(i + 1) % m]
            dist = math.hypot(x1 - x2, y1 - y2)
            min_sep = min(min_sep, dist)
            if dist < 0.1:
                overlap_pairs += 1
    return min_sep, overlap_pairs

def hash_stats(coords, label):
    n = len(coords)
    digests = [hashlib.sha256(f"{x},{y}".encode()).digest() for x, y in coords]
    dup_coords = n - len(set(coords))
    unique_full = len(set(digests))
    print(f"  [{label}] n={n}  duplicate coords={dup_coords}  unique full SHA-256={unique_full}")
    total_pairs = n * (n - 1) // 2
    for b in PREFIX_BYTES:
        counts = Counter(d[:b] for d in digests)
        pairs = sum(k * (k - 1) // 2 for k in counts.values())
        groups = sum(1 for k in counts.values() if k > 1)
        expected = total_pairs / (256 ** b)
        print(f"    prefix {b} byte(s): colliding pairs={pairs} (groups={groups}) "
              f"| birthday-expected ~{expected:.4g} | ratio {pairs/expected:.2f}x")

def run(step, label, max_depth):
    nodes = generate(step, max_depth)
    coords = [(x, y) for _, _, x, y in nodes]
    min_sep, overlaps = geometry_stats(nodes)
    print(f"{label}  step={step:.6f}  depth<={max_depth}")
    print(f"  geometry: min same-depth separation={min_sep:.4f}  overlap pairs (<0.1)={overlaps}")
    hash_stats(coords, "lattice")
    ctrl = random_control(len(coords))
    hash_stats(ctrl, "control")
    print()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--depth", type=int, default=12)
    args = ap.parse_args()
    if args.depth > 20:
        print("Warning: depth > 20 means >2M nodes; counts stay O(n) but memory grows.")
    print(f"GEOMETRIC HASH ANOMALY TEST (fixed) — R_inf={R_INF}\n")
    run(USER_STEP, "USER STEP", args.depth)
    run(IDEAL_STEP, "IDEAL STEP (ln 2)", args.depth)
    print("Reading: a hash anomaly exists only if the lattice counts deviate")
    print("materially from BOTH the birthday expectation AND the random control.")
