"""Turn the requirement sentences of a chat prompt into checkable requirements.

Deterministic patterns for the phrasings workflow prompts actually use. Every sentence that no
pattern understands is returned as NOT CHECKED, so silence never looks like a pass.
"""
import re

AMT = r"\$?\s*([\d,.]+)\s*(k|m|thousand|million)?"


def _num(v, unit):
    x = float(v.replace(",", ""))
    return x * {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6}.get((unit or "").lower(), 1)


def _node(wf, words):
    words = [w.lower() for w in words if len(w) > 2]
    best, score = None, 0
    for n, d in wf["nodes"].items():
        lab = d.get("label", n).lower()
        s = sum(w in lab for w in words)
        if s > score:
            best, score = n, s
    return best


def parse(prompt, wf):
    reqs, unchecked = [], []
    for sent in re.split(r"(?<=[.!?])\s+", prompt.strip()):
        s = sent.strip()
        low = s.lower()
        m = re.search(r"\((>=|>|over|above|more than)\s*" + AMT + r"\)\s*require[s]?\s+([\w ]+?)(?:\.|,|$)", low) or \
            re.search(r"(?:over|above|more than|>=|>)\s*" + AMT + r"[^,.]*?require[s]?\s+([\w ]+?)(?:\.|,|$)", low)
        if m:
            g = m.groups()
            op = ">=" if g[0] == ">=" else ">"
            amount = _num(g[-3], g[-2]) if len(g) == 4 else _num(g[0], g[1])
            step = _node(wf, g[-1].split())
            if step:
                reqs.append({"kind": "must_visit", "step": step, "when": f"amount {op} {amount:.0f}", "text": s})
                continue
        m = re.search(r"all applications need (.+?)(?:\.|$)", low)
        if m:
            for part in re.split(r",| and ", m.group(1)):
                step = _node(wf, part.split())
                if step:
                    reqs.append({"kind": "must_visit", "step": step, "text": s})
            continue
        m = re.search(r"if (\w+) issues are found, (.+?)(?:\.|$)", low)
        if m:
            step = _node(wf, m.group(2).split())
            if step:
                reqs.append({"kind": "must_visit", "step": step, "when": f"{m.group(1)} == 'issues'", "text": s})
                continue
        m = re.search(r"every (\w+) must be reviewed by an? (\w+) before", low)
        if m:
            step = _node(wf, [m.group(2)])
            if step:
                reqs.append({"kind": "must_visit", "step": step, "text": s})
                reqs.append({"kind": "before", "first": step, "text": s})
                continue
        if re.search(r"\blog everything\b|\baudit\b", low):
            reqs.append({"kind": "audit", "text": s})
            continue
        if re.search(r"\b(must|need|require|every|all|only|never)\b", low):
            unchecked.append(s)
    return reqs, unchecked
