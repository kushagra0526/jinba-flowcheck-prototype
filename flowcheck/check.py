"""Check a workflow against requirements (from its prompt) and deployment policy.

Every finding carries a concrete witness input (e.g. amount = 250,000, compliance = 'ok') so the
builder can see the exact case, not a vague warning.
"""
from .model import EXTERNAL, Interval, State, edge_atoms, out_edges, parse_when, paths


def _label(wf, n):
    return wf["nodes"][n].get("label", n)


def _fmt(w):
    def num(v):
        return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"
    return ", ".join(f"{k} = {num(v)}" if isinstance(v, float) else f"{k} = '{v}'" for k, v in w.items())


def check_structure(wf):
    found = []
    ps, cycles = paths(wf)
    on_path = {n for trail, _ in ps for n in trail}
    for n, d in wf["nodes"].items():
        if n not in on_path and d["type"] != "trigger":
            found.append({"code": "UNREACHABLE", "nodes": [n], "detail": f"'{_label(wf, n)}' can never run"})
        if not out_edges(wf, n) and d["type"] != "end":
            found.append({"code": "DEAD_END", "nodes": [n], "detail": f"'{_label(wf, n)}' has no next step and is not an end"})
        if d.get("wait") and not any(e.get("when") == "timeout" for e in out_edges(wf, n)):
            found.append({"code": "UNBOUNDED_WAIT", "nodes": [n],
                          "detail": f"'{_label(wf, n)}' waits on a person with no timeout path: a case can stall forever, unlogged"})
    seen = set()
    for c in cycles:
        key = frozenset(c)
        if key in seen:
            continue
        seen.add(key)
        if not any(wf["nodes"][n].get("max_iterations") for n in c):
            found.append({"code": "CYCLE", "nodes": c[:-1], "detail": "loop " + " → ".join(_label(wf, n) for n in c) + " has no iteration limit"})
    return found


def check_branches(wf):
    """Gaps and overlaps between sibling conditions (the classic > vs >= mistake)."""
    found = []
    for n, d in wf["nodes"].items():
        if d["type"] != "condition":
            continue
        ea = [(e, a) for e, a in edge_atoms(wf, n)]
        if any(a == "else" or a is None for _, a in ea):
            conds = [(e, a) for e, a in ea if a not in ("else", None)]
            has_else = True
        else:
            conds, has_else = ea, False
        base = State(wf["fields"])
        regions = [(e, base.apply(a)) for e, a in conds]
        for i in range(len(regions)):
            for j in range(i + 1, len(regions)):
                a, b = regions[i][1], regions[j][1]
                if a is None or b is None:
                    continue
                both = a.apply([(f, "num", iv) for f, iv in b.num.items()] + [(f, "enum", v) for f, v in b.enum.items()])
                if both is not None:
                    found.append({"code": "BRANCH_OVERLAP", "nodes": [n],
                                  "detail": f"'{_label(wf, n)}': both '{regions[i][0]['when']}' and '{regions[j][0]['when']}' match",
                                  "witness": both.witness()})
        if not has_else:
            from .model import _complement
            rest = _complement(base, [a for _, a in conds])
            for st in rest:
                found.append({"code": "BRANCH_GAP", "nodes": [n],
                              "detail": f"'{_label(wf, n)}': no branch matches {st.describe()}", "witness": st.witness()})
                break
    return found


def check_requirements(wf, reqs):
    found = []
    ps, _ = paths(wf)
    nodes = wf["nodes"]
    for r in reqs:
        k = r["kind"]
        for trail, st in ps:
            cond_st = st
            if r.get("when"):
                cond_st = st.apply(parse_when(r["when"], wf["fields"]))
                if cond_st is None:
                    continue
            if k == "must_visit" and r["step"] not in trail:
                found.append({"code": "MISSING_STEP", "nodes": [r["step"]], "req": r["text"],
                              "detail": f"'{_label(wf, r['step'])}' is skipped on path " + " → ".join(_label(wf, n) for n in trail),
                              "witness": cond_st.witness()})
                break
            if k == "before":
                first = trail.index(r["first"]) if r["first"] in trail else None
                for i, n in enumerate(trail):
                    if nodes[n].get("decision") and (first is None or i < first):
                        found.append({"code": "ORDER_VIOLATION", "nodes": [n, r["first"]], "req": r["text"],
                                      "detail": f"'{_label(wf, n)}' happens " + ("without" if first is None else "before")
                                                + f" '{_label(wf, r['first'])}'", "witness": cond_st.witness()})
                        break
                else:
                    continue
                break
            if k == "audit":
                last_effect = max([i for i, n in enumerate(trail) if nodes[n].get("decision") or nodes[n]["type"] in EXTERNAL] or [-1])
                if not any(nodes[n].get("log") for n in trail[last_effect + 1:] if True) and \
                        not (last_effect >= 0 and nodes[trail[last_effect]].get("log")):
                    found.append({"code": "AUDIT_GAP", "nodes": [trail[-1]], "req": r["text"],
                                  "detail": "path ends without an audit log entry: " + " → ".join(_label(wf, n) for n in trail),
                                  "witness": cond_st.witness()})
                    break
    return found


def check_privacy(wf, policy):
    found = []
    pii = {f for f, s in wf["fields"].items() if s == "pii"}
    for n, d in wf["nodes"].items():
        leaked = sorted(set(d.get("reads", [])) & pii)
        if not leaked:
            continue
        if d["type"] == "llm" and d.get("model", "external") != "private" and policy.get("llm") == "private-only":
            found.append({"code": "PII_EGRESS", "nodes": [n], "detail": f"'{_label(wf, n)}' sends {', '.join(leaked)} to a model not marked private" + (" (external)" if d.get("model") == "external" else " (hosting unspecified)")})
        elif d["type"] in EXTERNAL and not set(leaked) <= set(d.get("allow_pii", [])):
            found.append({"code": "PII_EGRESS", "nodes": [n], "detail": f"'{_label(wf, n)}' ({d['type']}) sends {', '.join(leaked)} outside"})
    return found


def check(wf, reqs, policy=None):
    f = check_structure(wf) + check_branches(wf) + check_requirements(wf, reqs) + check_privacy(wf, policy or {})
    for x in f:
        if "witness" in x and x["witness"]:
            x["witness_text"] = _fmt(x["witness"])
    return f
