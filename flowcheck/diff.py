"""Explain an edit in plain words: for which inputs does the set of guaranteed steps change?

Witness inputs are every numeric constant in either version (and just above/below it) crossed
with every enum value; events (reply/timeout) are explored by path enumeration.
"""
from itertools import product

from .model import paths


def _constants(wf):
    import re
    vals = set()
    for e in wf["edges"]:
        for m in re.finditer(r"(\w+)\s*(?:>=|<=|==|!=|>|<)\s*([\d.]+)", e.get("when") or ""):
            vals.add((m.group(1), float(m.group(2))))
    return vals


def _contains(state, inp):
    for f, iv in state.num.items():
        x = inp[f]
        if not any((lo < x or (lc and lo == x)) and (x < hi or (hc and x == hi)) for lo, lc, hi, hc in iv.parts):
            return False
    return all(inp[f] in v for f, v in state.enum.items())


def _guaranteed(ps, inp):
    trails = [set(t) for t, st in ps if _contains(st, inp)]
    return set.intersection(*trails) if trails else set()


def explain(a, b):
    fields = a["fields"]
    consts = _constants(a) | _constants(b)
    nums = {f: sorted({0.0} | {v + d for (g, v) in consts if g == f for d in (-1, 0, 0.5, 1)})
            for f, s in fields.items() if s == "number"}
    enums = {f: s[1:] for f, s in fields.items() if isinstance(s, list) and s[0] == "enum"}
    keys = list(nums) + list(enums)
    pa, pb = paths(a)[0], paths(b)[0]
    changes = {}
    for combo in product(*[nums[k] for k in nums], *[enums[k] for k in enums]):
        inp = dict(zip(keys, combo))
        ga, gb = _guaranteed(pa, inp), _guaranteed(pb, inp)
        for n in ga - gb:
            changes.setdefault(("no longer guaranteed", n), []).append(inp)
        for n in gb - ga:
            changes.setdefault(("now always runs", n), []).append(inp)
    out = []
    for (what, n), inps in sorted(changes.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        node = (b["nodes"].get(n) or a["nodes"].get(n))
        ex = inps[0]
        desc = ", ".join(f"{k} = {v:,.2f}".replace(".00", "") if isinstance(v, float) else f"{k} = '{v}'" for k, v in ex.items())
        out.append({"change": what, "step": node.get("label", n), "inputs": len(inps), "example": desc})
    return out
