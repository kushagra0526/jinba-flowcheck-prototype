"""Check Jinba's published loan-screening example, then 13 chat edits → out/report.html, out/report.json

    python demo.py
"""
import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from flowcheck.baseline import sample_test  # noqa: E402
from flowcheck.check import check  # noqa: E402
from flowcheck.diff import explain  # noqa: E402
from flowcheck.examples import ASSUMED, BENIGN, MUTATIONS, PROMPT, published, reference  # noqa: E402
from flowcheck.reqs import parse  # noqa: E402

POLICY = {"llm": "private-only"}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    pub = published()
    reqs, unchecked = parse(PROMPT, pub)
    print("\n== Requirements read from the prompt on jinba.io/uses/loan-screening")
    for r in reqs:
        print(f"   {r['kind']:10s} {r.get('step') or r.get('first') or '':16s} {r.get('when') or '':22s} ← {r['text'][:70]}")
    print(f"   not checked: {unchecked or 'none'}")
    pf = check(pub, reqs, POLICY)
    print("\n== Published graph: questions for the builder (policy: PII only to private models)")
    for f in pf:
        print(f"   {f['code']:15s} {f['detail']}")
    print("   assumed (not visible on the page): " + "; ".join(ASSUMED))

    ref = reference()
    rows = []
    print("\n== 13 chat edits applied to a clean version of the same flow")
    for name, ask, fn, exp in MUTATIONS:
        wf = fn(ref)
        got = check(wf, reqs, POLICY)
        codes = {f["code"] for f in got}
        base = sample_test(wf, reqs)
        rows.append({"name": name, "ask": ask, "expected": sorted(exp), "found": sorted(codes), "caught": exp <= codes,
                     "exact": exp == codes, "baseline": bool(base & exp),
                     "first": next((f for f in got if f["code"] in exp), got[0] if got else None)})
        f0 = rows[-1]["first"]
        print(f"   {'✓' if exp <= codes else '✗'} {name:24s} {', '.join(sorted(codes)):32s} sample-run: {'caught' if base & exp else 'missed'}")
        if f0:
            print(f"       {f0['detail'][:110]}" + (f"  [{f0['witness_text']}]" if f0.get("witness_text") else ""))
    ben = [{"name": n, "ask": a, "found": [f["code"] for f in check(fn(ref), reqs, POLICY)]} for n, a, fn in BENIGN]
    print(f"\n   benign edits flagged: {sum(bool(b['found']) for b in ben)} of {len(ben)}")
    caught = sum(r["caught"] for r in rows)
    base = sum(r["baseline"] for r in rows)
    print(f"   flowcheck {caught}/{len(rows)} · sample-run test {base}/{len(rows)}")

    edit = next(m for m in MUTATIONS if m[0] == "existing-customer-fastlane")
    d = explain(ref, edit[2](ref))
    print(f"\n== Edit explained: \"{edit[1]}\"")
    for c in d:
        print(f"   {c['change']}: {c['step']}  (e.g. {c['example']})")

    data = {"prompt": PROMPT, "requirements": reqs, "unchecked": unchecked, "published_findings": pf, "assumed": ASSUMED,
            "mutations": rows, "benign": ben, "caught": caught, "baseline": base, "diff_edit": edit[1], "diff": d}
    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out" / "report.json").write_text(json.dumps(data, indent=1, default=str))
    (HERE / "out" / "report.html").write_text(render(data), encoding="utf-8")
    print("\nwrote out/report.html, out/report.json")


def render(d):
    e = html.escape
    req = "".join(f"<tr><td><b>{e(r['kind'])}</b></td><td>{e(r.get('step') or r.get('first') or '')}</td>"
                  f"<td class='mono'>{e(r.get('when') or '')}</td><td class='mute'>“{e(r['text'])}”</td></tr>" for r in d["requirements"])
    pub = "".join(f"<li><b>{e(f['code'])}</b> {e(f['detail'])}</li>" for f in d["published_findings"])
    mut = "".join(
        f"<tr><td>{'<b class=good>✓</b>' if r['caught'] else '<b class=bad>✗</b>'}</td><td>“{e(r['ask'])}”</td>"
        f"<td class='mono'>{e(', '.join(r['found']))}</td><td>{e((r['first'] or {}).get('detail', '')[:95])}"
        f"{'<br><span class=mute>' + e(r['first'].get('witness_text', '')) + '</span>' if r['first'] and r['first'].get('witness_text') else ''}</td>"
        f"<td class='{'good' if r['baseline'] else 'bad'}'>{'caught' if r['baseline'] else 'missed'}</td></tr>" for r in d["mutations"])
    diff = "".join(f"<li><b>{e(c['change'])}</b>: {e(c['step'])} <span class='mute'>(e.g. {e(c['example'])})</span></li>" for c in d["diff"])
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>flowcheck report</title><style>
:root{{--bg:#0f1115;--card:#171a21;--ink:#e8eaf0;--mute:#9aa3b2;--line:#2a2f3a;--good:#4ade80;--bad:#f87171}}
body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,Segoe UI,sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:26px 16px}} h1{{font-size:24px;margin:0 0 4px}} h2{{font-size:16px;margin:0 0 8px}}
.sub,.mute{{color:var(--mute)}} .sub{{margin-bottom:14px}} section{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:12px}}
table{{border-collapse:collapse;width:100%;font-size:13px}} td,th{{border-bottom:1px solid var(--line);padding:5px 7px;text-align:left;vertical-align:top}} th{{color:var(--mute);font-weight:500}}
.mono{{font-family:Consolas,monospace;font-size:12px}} .good{{color:var(--good)}} .bad{{color:var(--bad)}} ul{{margin:4px 0;padding-left:18px}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:12px}} @media(max-width:860px){{.two{{grid-template-columns:1fr}}}}
</style></head><body><main>
<h1>flowcheck: verify a chat-built workflow against its own prompt</h1>
<div class="sub">Input: the loan-screening example on jinba.io/uses/loan-screening (prompt + published graph). Policy: PII only to private models.
Unofficial prototype by Kushagra Pratap Singh. Not affiliated with Jinba.</div>
<section><h2>Requirements read from the prompt ({len(d['requirements'])} checkable, {len(d['unchecked'])} not understood)</h2>
<table><tr><th>kind</th><th>step</th><th>only when</th><th>from</th></tr>{req}</table></section>
<section><h2>The published graph: what the builder should confirm</h2><ul>{pub}</ul>
<p class="mute">Not visible on the page, so assumed: {e('; '.join(d['assumed']))}</p></section>
<section><h2>13 chat edits: flowcheck {d['caught']}/13 · four sample applications {d['baseline']}/13 · benign edits flagged {sum(bool(b['found']) for b in d['benign'])}/{len(d['benign'])}</h2>
<table><tr><th></th><th>edit asked in chat</th><th>findings</th><th>first finding · witness input</th><th>sample run</th></tr>{mut}</table></section>
<section><h2>Edit explained for compliance: “{e(d['diff_edit'])}”</h2><ul>{diff}</ul></section>
</main></body></html>"""


if __name__ == "__main__":
    main()
