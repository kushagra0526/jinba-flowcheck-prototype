"""What a typical pre-deploy test run would catch: 4 sample applications, check the prompt's
requirements on the steps they actually visit. No structural, privacy or boundary analysis."""
from .model import paths, parse_when

SAMPLES = [{"amount": 100000.0, "compliance": c, "customer": "new"} for c in ("ok", "issues")] + \
          [{"amount": 500000.0, "compliance": c, "customer": "new"} for c in ("ok", "issues")]


def _runs(wf, inp):
    from .diff import _contains
    return [t for t, st in paths(wf)[0] if _contains(st, inp)]


def sample_test(wf, reqs):
    """Returns set of finding codes the sample run surfaces."""
    codes = set()
    for inp in SAMPLES:
        runs = _runs(wf, inp)
        if not runs:
            codes.add("NO_PATH")
            continue
        for r in reqs:
            if r.get("when"):
                f, _, v = parse_when(r["when"], wf["fields"])[0]
                ok = (f in inp) and ((v.parts and any(lo < inp[f] <= hi or (lc and lo == inp[f]) for lo, lc, hi, hc in v.parts))
                                     if _ == "num" else inp[f] in v)
                if not ok:
                    continue
            for t in runs:
                if r["kind"] == "must_visit" and r["step"] not in t:
                    codes.add("MISSING_STEP")
                if r["kind"] == "before":
                    first = t.index(r["first"]) if r["first"] in t else 10 ** 9
                    if any(wf["nodes"][n].get("decision") and i < first for i, n in enumerate(t)):
                        codes.add("ORDER_VIOLATION")
    return codes
