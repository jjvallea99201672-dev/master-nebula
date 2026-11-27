#!/usr/bin/env python3
# Copyright (c) 2026 Jay Vallea (JAY.01). All rights reserved.
"""
concave_earth_node.py -- Option 1: flat-ball / hard-shell / flat-interior 7-node model.

WHAT THIS IS (plainly):
    A GEOMETRIC TOY MODEL. A rigid spherical shell of radius R = 1 (units of R_earth)
    encloses a flat (Ricci-flat, i.e. ordinary Euclidean) interior. A "Sun" disc of
    radius R_sun sits at the origin. Seven nodes sit on a ring of radius rho inside the
    shell (or on the shell itself in concave-Earth mode). Straight chords between nodes
    are links unless the Sun blocks them.

    It is NOT a claim about the real Earth or the real Sun. The real Earth is a convex
    oblate spheroid and the real Sun is ~109 Earth radii across and ~23,500 Earth radii
    away. The "concave-Earth" option is a geometry exercise only.

Design choices (documented):
    * Occlusion is NON-STRICT: a chord whose closest approach to the origin equals R_sun
      (tangent / grazing) counts as BLOCKED. So chord k is lost when R_sun >= rho*cos(pi*k/7),
      i.e. when x = R_sun/rho >= cos(pi*k/7). This matches the regime boundaries
      "cos(3pi/7) <= x" etc.
    * Only the closest approach of the chord SEGMENT matters; for a chord between two
      points on a centred ring the closest point is the midpoint, distance rho*cos(pi*k/7).
    * Thresholds are computed from math.cos, never hard-coded.
    * Shell mode (node_ring_radius=None): nodes at rho = R. There are no interior chords
      by construction; links are shell arcs between neighbours (k = 1) only -> 7 edges.
      Arcs run along the shell, so Sun occlusion does not apply to them (the Sun is inside,
      R_sun < R is required). Echo gap uses 2R/c. The chord/arc fingerprint ratio for the
      actual links is 1 (an arc measured against itself); the would-be chord/arc ratio is
      still reported for reference only.
    * echo_gap = 2*rho/c is the light time across the ring diameter (node -> through
      centre -> antipodal point of the ring). Uniform for all nodes by symmetry.
    * n is fixed at 7. Heptagram {7/k} needs n prime so each {7/2}, {7/3} is a single
      connected star polygon; n != 7 or non-prime n raises ValueError.
    * Near-critical default is SURFACED, not tuned away: rho = 0.90, R_sun = 0.20 gives
      x = 0.2222..., about 2.99e-4 below the heptagram break cos(3pi/7) = 0.222521.
"""
import math
from collections import deque

N_NODES = 7
R_EARTH_KM = 6371.0
C_KM_S = 299792.458
NEAR_CRITICAL_EPS = 1e-3


def is_prime(n):
    if n < 2:
        return False
    return all(n % d for d in range(2, int(math.isqrt(n)) + 1))


def enforce_prime7(n):
    """Heptagram mode requires n = 7 (prime). Raise ValueError otherwise."""
    if not is_prime(n):
        raise ValueError(f"heptagram mode needs prime n; n={n} is not prime "
                         "({n}/k star polygons would split into multiple components)")
    if n != N_NODES:
        raise ValueError(f"model is fixed at n=7; got n={n}")
    return n


def thresholds(n=N_NODES):
    """x thresholds at which chord class k is lost: cos(pi*k/n), k=3,2,1 (ascending)."""
    enforce_prime7(n)
    return {k: math.cos(math.pi * k / n) for k in (3, 2, 1)}


REGIMES = ("full K7", "heptagram {7/3} broken", "ring only", "all via Sun")


class BeaconDark(Exception):
    """Raised when the Sun-relayed beacon path is unavailable."""


class ConcaveEarthNode:
    def __init__(self, node_ring_radius=0.90, R_sun=0.20, R_shell=1.0, n=N_NODES,
                 c_km_s=C_KM_S, r_earth_km=R_EARTH_KM, beacon_dark=False):
        enforce_prime7(n)
        self.n = n
        self.R = float(R_shell)
        self.shell_mode = node_ring_radius is None
        self.rho = self.R if self.shell_mode else float(node_ring_radius)
        self.R_sun = float(R_sun)
        self.c = c_km_s
        self.r_earth_km = r_earth_km
        self.beacon_dark = beacon_dark
        if not (0 < self.rho <= self.R):
            raise ValueError("need 0 < rho <= R")
        if self.R_sun < 0:
            raise ValueError("R_sun must be >= 0")
        if self.shell_mode and self.R_sun >= self.R:
            raise ValueError("shell mode needs R_sun < R (Sun inside the shell)")

    # --- geometry -------------------------------------------------------
    @property
    def x(self):
        return self.R_sun / self.rho

    def positions(self):
        return [(self.rho * math.cos(2 * math.pi * i / self.n),
                 self.rho * math.sin(2 * math.pi * i / self.n)) for i in range(self.n)]

    def clearance(self, k):
        """Closest approach of a |k| chord to the origin: rho*cos(pi*k/7)."""
        return self.rho * math.cos(math.pi * k / self.n)

    def chord_open(self, k):
        """Non-strict occlusion: blocked iff R_sun >= clearance."""
        if self.shell_mode:
            return False  # no interior chords in shell mode
        return self.R_sun < self.clearance(k)

    @staticmethod
    def sep(i, j, n=N_NODES):
        d = abs(i - j) % n
        return min(d, n - d)

    def link_graph(self):
        """Surviving edges (i<j) computed from geometry. Never assumes K7."""
        edges = []
        for i in range(self.n):
            for j in range(i + 1, self.n):
                k = self.sep(i, j, self.n)
                if self.shell_mode:
                    if k == 1:
                        edges.append((i, j))   # shell arc, not occluded
                elif self.chord_open(k):
                    edges.append((i, j))
        return edges

    def regime(self):
        if self.shell_mode:
            return "shell arcs only (concave-Earth)"
        t = thresholds(self.n)
        x = self.x
        if x < t[3]:
            return REGIMES[0]
        if x < t[2]:
            return REGIMES[1]
        if x < t[1]:
            return REGIMES[2]
        return REGIMES[3]

    def criticality(self, eps=NEAR_CRITICAL_EPS):
        """Regime, nearest transition, signed margin (threshold - x; + means below it)."""
        if self.shell_mode:
            return {"regime": self.regime(), "nearest_transition": None,
                    "margin": None, "near_critical": False,
                    "note": "occlusion does not apply to shell arcs"}
        t = thresholds(self.n)
        names = {3: "heptagram break (k=3 lost)", 2: "ring-only (k=2 lost)",
                 1: "all-via-Sun (k=1 lost)"}
        k_near = min(t, key=lambda k: abs(t[k] - self.x))
        margin = t[k_near] - self.x
        return {"regime": self.regime(), "x": self.x,
                "nearest_transition": names[k_near], "threshold": t[k_near],
                "margin": margin, "near_critical": abs(margin) < eps}

    def fingerprint(self):
        """Per k: chord 2*rho*sin(pi k/7), shell arc R*(2 pi k/7), ratio chord/arc."""
        out = {}
        for k in (1, 2, 3):
            chord = 2 * self.rho * math.sin(math.pi * k / self.n)
            arc = self.R * (2 * math.pi * k / self.n)
            out[k] = {"chord": chord, "arc": arc, "ratio": chord / arc}
            if self.shell_mode:
                out[k]["link_ratio"] = 1.0 if k == 1 else None  # arc vs itself; k>1 not links
        return out

    def echo_gap(self):
        dimless = 2 * self.rho  # in units R_earth / c
        seconds = dimless * self.r_earth_km / self.c
        return {"dimensionless_2rho_over_c": dimless, "seconds": seconds}

    # --- comms ----------------------------------------------------------
    def beacon_relay(self, src, dst):
        if self.beacon_dark:
            raise BeaconDark("Sun-relayed beacon is dark")
        return [src, "SUN", dst]

    def degrade_to_gossip(self, source=0):
        """BFS hop counts over surviving edges only."""
        adj = {i: set() for i in range(self.n)}
        for i, j in self.link_graph():
            adj[i].add(j)
            adj[j].add(i)
        hops = {source: 0}
        q = deque([source])
        while q:
            u = q.popleft()
            for v in sorted(adj[u]):
                if v not in hops:
                    hops[v] = hops[u] + 1
                    q.append(v)
        return {"source": source, "hops": hops,
                "unreachable": [i for i in range(self.n) if i not in hops],
                "connected": len(hops) == self.n,
                "edges": len(self.link_graph())}

    def route(self, src, dst):
        try:
            return {"mode": "beacon", "path": self.beacon_relay(src, dst)}
        except BeaconDark:
            g = self.degrade_to_gossip(src)
            return {"mode": "gossip", "hops": g["hops"].get(dst), "connected": g["connected"]}


# --- checks ---------------------------------------------------------------
def run_checks(verbose=True):
    results = []

    def check(name, cond):
        results.append((name, bool(cond)))
        if verbose:
            print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

    t = thresholds()
    check("thresholds monotone cos(3pi/7)<cos(2pi/7)<cos(pi/7)", t[3] < t[2] < t[1])
    rho = 0.9
    delta = 1e-9
    samples = [(0.0, 21), (0.1, 21), (t[3] - delta, 21), (t[3], 14), (t[3] + delta, 14),
               (0.4, 14), (t[2] - delta, 14), (t[2], 7), (t[2] + delta, 7), (0.8, 7),
               (t[1] - delta, 7), (t[1], 0), (t[1] + delta, 0), (1.0, 0)]
    for x, want in samples:
        m = ConcaveEarthNode(node_ring_radius=rho, R_sun=x * rho)
        # x exactly at threshold: compare with clearance directly (float-safe)
        got = len(m.link_graph())
        check(f"x={x:.10f}: {want} edges ({m.regime()})", got == want)

    d = ConcaveEarthNode()
    c = d.criticality()
    check("default regime is full K7", c["regime"] == "full K7")
    check(f"default margin ~2.99e-4 (got {c['margin']:.6e})",
          abs(c["margin"] - 2.99e-4) < 1e-6)
    check("default flagged near_critical", c["near_critical"])
    for x, _ in samples:
        m = ConcaveEarthNode(node_ring_radius=rho, R_sun=x * rho)
        E = m.link_graph()
        S = set(E) | {(j, i) for i, j in E}
        ok = all((j, i) in S for i, j in S) and all(i != j for i, j in E)
        if not ok:
            break
    check("graphs symmetric, no self loops (all samples)", ok)
    fp = d.fingerprint()
    check("chord < arc for k=1,2,3", all(fp[k]["chord"] < fp[k]["arc"] for k in fp))
    for x, label, want in [(0.1, "K7", True), (0.4, "heptagram-broken", True),
                           (0.8, "ring-only", True), (0.95, "all-via-Sun", False)]:
        m = ConcaveEarthNode(node_ring_radius=rho, R_sun=x * rho, beacon_dark=True)
        g = m.degrade_to_gossip(0)
        check(f"gossip {label}: connected={want}", g["connected"] == want)
    m = ConcaveEarthNode(node_ring_radius=rho, R_sun=0.95 * rho, beacon_dark=True)
    check("all-via-Sun + BeaconDark: empty graph", m.degrade_to_gossip(0)["edges"] == 0)
    try:
        m.beacon_relay(0, 3)
        raised = False
    except BeaconDark:
        raised = True
    check("BeaconDark raised when beacon dark", raised)
    s = ConcaveEarthNode(node_ring_radius=None)
    E = s.link_graph()
    check("shell mode: 7 arc edges, all k=1 (no chords)",
          len(E) == 7 and all(ConcaveEarthNode.sep(i, j) == 1 for i, j in E))
    check("shell mode: gossip connected", s.degrade_to_gossip(0)["connected"])
    check("shell mode: echo gap uses 2R", abs(s.echo_gap()["dimensionless_2rho_over_c"] - 2.0) < 1e-12)
    for bad in (6, 8):
        try:
            ConcaveEarthNode(n=bad)
            r = False
        except ValueError:
            r = True
        check(f"prime-7 enforcement raises for n={bad}", r)
    passed = sum(ok for _, ok in results)
    if verbose:
        print(f"\n  TOTAL: {passed}/{len(results)} PASS")
    return passed, len(results)


def summary():
    print("=" * 72)
    print("concave_earth_node.py -- GEOMETRIC TOY MODEL (not the real Earth/Sun)")
    print("=" * 72)
    t = thresholds()
    print("\nRegimes by x = R_sun/rho (non-strict occlusion: tangent = blocked)")
    print(f"  {'regime':28s} {'x range':40s} edges")
    print(f"  {REGIMES[0]:28s} {'x < cos(3pi/7) = %.6f' % t[3]:40s} 21")
    print(f"  {REGIMES[1]:28s} {'%.6f <= x < cos(2pi/7) = %.6f' % (t[3], t[2]):40s} 14")
    print(f"  {REGIMES[2]:28s} {'%.6f <= x < cos(pi/7) = %.6f' % (t[2], t[1]):40s} 7")
    print(f"  {REGIMES[3]:28s} {'x >= %.6f' % t[1]:40s} 0")
    d = ConcaveEarthNode()
    c = d.criticality()
    print(f"\nDefault: rho=0.90, R_sun=0.20, x={c['x']:.6f}")
    print(f"  regime={c['regime']}, edges={len(d.link_graph())}")
    print(f"  nearest transition: {c['nearest_transition']} at {c['threshold']:.6f}")
    print(f"  margin = {c['margin']:+.6e}")
    if c["near_critical"]:
        print(f"  WARNING: NEAR-CRITICAL (|margin| < {NEAR_CRITICAL_EPS}). Increasing R_sun by "
              f"{c['margin'] * d.rho:.3e} R_earth (~{c['margin'] * d.rho * R_EARTH_KM:.2f} km) "
              "drops all 7 {7/3} chords.")
    print("\nFingerprint (rho=0.90, R=1): chord 2 rho sin(pi k/7) vs shell arc 2 pi k R/7")
    for k, v in d.fingerprint().items():
        print(f"  k={k}: chord={v['chord']:.6f} arc={v['arc']:.6f} ratio={v['ratio']:.6f}"
              f"  clearance={d.clearance(k):.6f} open={d.chord_open(k)}")
    e = d.echo_gap()
    print(f"\nEcho gap 2 rho/c: {e['dimensionless_2rho_over_c']:.4f} R_earth/c = "
          f"{e['seconds'] * 1e3:.4f} ms (uniform for all 7 nodes)")
    s = ConcaveEarthNode(node_ring_radius=None)
    es = s.echo_gap()
    print("\nShell mode (node_ring_radius=None, strict concave-Earth):")
    print(f"  edges={len(s.link_graph())} (k=1 shell arcs only, no interior chords)")
    print("  Sun occlusion: not applicable to arcs along the shell")
    print(f"  echo gap 2R/c = {es['dimensionless_2rho_over_c']:.4f} R_earth/c = {es['seconds'] * 1e3:.4f} ms")
    print("  link fingerprint ratio = 1 (arc vs itself); reference chord/arc at rho=R: " +
          ", ".join(f"k={k}:{v['ratio']:.6f}" for k, v in s.fingerprint().items()))
    print("\nStructural checks:")
    return run_checks()


if __name__ == "__main__":
    p, n = summary()
    raise SystemExit(0 if p == n else 1)
