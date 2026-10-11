"""Workflow graph + edge conditions, and path enumeration with symbolic inputs.

A workflow is a dict:
  fields: {name: "number" | "pii" | "text" | ["enum", "values"...]}
  nodes:  {id: {type, label, reads: [fields], model?: "private"|"external", log?: bool,
                decision?: bool, wait?: bool, timeout?: node_id}}
  edges:  [{from, to, when?: "amount > 250000" | "compliance == 'issues'" | "else" | "timeout"}]

Conditions are a deliberately small language (what chat-built flows actually use): comparisons
of one numeric field with a constant, enum equality / inequality, `and`, and `else`.
"""
import math
import re

TERMINAL = {"end"}
EXTERNAL = {"http", "slack", "email", "webhook"}
CMP = re.compile(r"^\s*(\w+)\s*(>=|<=|==|!=|>|<)\s*('?)([^']*?)\3\s*$")


class Interval:
    """Union of half-open/closed intervals on the real line, as a sorted list of (lo, lo_closed, hi, hi_closed)."""

    def __init__(self, parts=None):
        self.parts = parts if parts is not None else [(-math.inf, False, math.inf, False)]

    @staticmethod
    def of(op, v):
        if op == ">":
            return Interval([(v, False, math.inf, False)])
        if op == ">=":
            return Interval([(v, True, math.inf, False)])
        if op == "<":
            return Interval([(-math.inf, False, v, False)])
        if op == "<=":
            return Interval([(-math.inf, False, v, True)])
        if op == "==":
            return Interval([(v, True, v, True)])
        if op == "!=":
            return Interval([(-math.inf, False, v, False), (v, False, math.inf, False)])
        raise ValueError(op)

    def intersect(self, other):
        out = []
        for a in self.parts:
            for b in other.parts:
                lo, loc = max((a[0], not a[1]), (b[0], not b[1]))
                lo_closed = not loc
                hi, hic = min((a[2], a[3]), (b[2], b[3]))
                if lo < hi or (lo == hi and lo_closed and hic):
                    out.append((lo, lo_closed, hi, hic))
        return Interval(out)

    def empty(self):
        return not self.parts

    def witness(self):
        """A concrete value inside (prefer boundaries, which is where mistakes live)."""
        for lo, lc, hi, hc in self.parts:
            if lc and lo != -math.inf:
                return lo
            if hc and hi != math.inf:
                return hi
            if lo == -math.inf and hi == math.inf:
                return 0.0
            if lo == -math.inf:
                return hi - 1
            if hi == math.inf:
                return lo + 1
            return (lo + hi) / 2
        return None

    def __repr__(self):
        def f(x):
            return "∞" if x == math.inf else "-∞" if x == -math.inf else f"{x:,.0f}"
        return " ∪ ".join(f"{'[' if lc else '('}{f(lo)}, {f(hi)}{']' if hc else ')'}" for lo, lc, hi, hc in self.parts) or "∅"


def parse_when(expr, fields):
    """→ list of atoms: (field, kind, value) ; kind 'num' with Interval, 'enum' with allowed set."""
    atoms = []
    for part in re.split(r"\s+and\s+", expr.strip()):
        m = CMP.match(part)
        if not m:
            raise ValueError(f"cannot read condition: {part!r}")
        f, op, quoted, val = m.groups()
        spec = fields.get(f)
        if spec == "number" and not quoted:
            atoms.append((f, "num", Interval.of(op, float(val))))
        elif isinstance(spec, list) and spec[0] == "enum":
            vals = set(spec[1:])
            if op == "==":
                atoms.append((f, "enum", {val}))
            elif op == "!=":
                atoms.append((f, "enum", vals - {val}))
            else:
                raise ValueError(f"enum field {f} compared with {op}")
        else:
            raise ValueError(f"unknown field or type in {part!r}")
    return atoms


class State:
    """Symbolic input: numeric fields as Interval, enums as allowed sets."""

    def __init__(self, fields, num=None, enum=None):
        self.fields = fields
        self.num = num or {f: Interval() for f, s in fields.items() if s == "number"}
        self.enum = enum or {f: set(s[1:]) for f, s in fields.items() if isinstance(s, list) and s[0] == "enum"}

    def apply(self, atoms):
        num, enum = dict(self.num), {k: set(v) for k, v in self.enum.items()}
        for f, kind, v in atoms:
            if kind == "num":
                num[f] = num[f].intersect(v)
                if num[f].empty():
                    return None
            else:
                enum[f] &= v
                if not enum[f]:
                    return None
        return State(self.fields, num, enum)

    def witness(self):
        w = {f: iv.witness() for f, iv in self.num.items()}
        w.update({f: sorted(v)[0] for f, v in self.enum.items()})
        return w

    def describe(self):
        out = [f"{f} ∈ {iv}" for f, iv in self.num.items() if repr(iv) != "(-∞, ∞)"]
        out += [f"{f} ∈ {{{', '.join(sorted(v))}}}" for f, v in self.enum.items()
                if v != set(self.fields[f][1:])]
        return ", ".join(out) or "any input"


def out_edges(wf, nid):
    return [e for e in wf["edges"] if e["from"] == nid]


def edge_atoms(wf, nid):
    """Resolve 'else' as the complement of the siblings (only for single-field numeric or enum splits)."""
    edges = out_edges(wf, nid)
    res = []
    for e in edges:
        w = e.get("when")
        if w is None or re.fullmatch(r"\w+", w) and w != "else":   # events: 'timeout', 'reply', ...
            res.append((e, None))
        elif w == "else":
            res.append((e, "else"))
        else:
            res.append((e, parse_when(w, wf["fields"])))
    return res


def paths(wf, max_paths=5000):
    """All trigger→terminal paths with the symbolic input they require. Cycles are cut and reported."""
    start = next(n for n, d in wf["nodes"].items() if d["type"] == "trigger")
    out, cycles = [], []

    def go(nid, state, trail):
        if len(out) >= max_paths:
            return
        if nid in trail:
            cycles.append(trail[trail.index(nid):] + [nid])
            return
        trail = trail + [nid]
        node = wf["nodes"][nid]
        edges = edge_atoms(wf, nid)
        if node["type"] in TERMINAL or not edges:
            out.append((trail, state))
            return
        taken = []
        for e, atoms in edges:
            if atoms is None:
                go(e["to"], state, trail)
            elif atoms == "else":
                # remaining region: subtract siblings by enumerating their complements per atom
                for st in _complement(state, taken):
                    go(e["to"], st, trail)
            else:
                taken.append(atoms)
                st = state.apply(atoms)
                if st is not None:
                    go(e["to"], st, trail)

    go(start, State(wf["fields"]), [])
    return out, cycles


def _negate(atoms):
    """¬(a1 ∧ a2 ∧ …) as a list of alternative single-atom constraints."""
    alts = []
    for f, kind, v in atoms:
        if kind == "num":
            comp = []
            prev_hi, prev_hc = -math.inf, False
            for lo, lc, hi, hc in v.parts:
                if prev_hi < lo or (prev_hi == lo and not prev_hc and not lc):
                    comp.append((prev_hi, not prev_hc if prev_hi != -math.inf else False, lo, not lc))
                prev_hi, prev_hc = hi, hc
            if prev_hi != math.inf:
                comp.append((prev_hi, not prev_hc, math.inf, False))
            alts.append([(f, "num", Interval(comp))])
        else:
            alts.append([(f, "enum", None, v)])
    return alts


def _complement(state, taken):
    """States where none of the `taken` sibling conditions hold."""
    states = [state]
    for atoms in taken:
        nxt = []
        for st in states:
            for alt in _negate(atoms):
                fixed = []
                for a in alt:
                    if a[1] == "enum" and len(a) == 4:
                        fixed.append((a[0], "enum", st.enum[a[0]] - a[3]))
                    else:
                        fixed.append(a)
                s2 = st.apply(fixed)
                if s2 is not None:
                    nxt.append(s2)
        states = nxt
    return states
