import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flowcheck.baseline import sample_test  # noqa: E402
from flowcheck.check import check  # noqa: E402
from flowcheck.diff import explain  # noqa: E402
from flowcheck.examples import BENIGN, MUTATIONS, PROMPT, published, reference  # noqa: E402
from flowcheck.model import Interval  # noqa: E402
from flowcheck.reqs import parse  # noqa: E402

POL = {"llm": "private-only"}


def test_prompt_requirements_are_all_understood():
    reqs, unchecked = parse(PROMPT, published())
    kinds = sorted(r["kind"] for r in reqs)
    assert unchecked == [] and kinds.count("must_visit") == 5 and "before" in kinds and "audit" in kinds
    assert any(r.get("when") == "amount > 250000" and r["step"] == "enhanced-screen" for r in reqs)


def test_reference_flow_is_clean_and_published_flow_raises_its_three_questions():
    reqs, _ = parse(PROMPT, published())
    assert check(reference(), reqs, POL) == []
    codes = sorted(f["code"] for f in check(published(), reqs, POL))
    assert codes == ["PII_EGRESS", "PII_EGRESS", "UNBOUNDED_WAIT"]


def test_every_labelled_mistake_is_caught_exactly_and_no_benign_edit_is_flagged():
    ref = reference()
    reqs, _ = parse(PROMPT, ref)
    for name, _, fn, exp in MUTATIONS:
        assert {f["code"] for f in check(fn(ref), reqs, POL)} == exp, name
    for name, _, fn in BENIGN:
        assert check(fn(ref), reqs, POL) == [], name


def test_gap_witness_is_the_exact_boundary():
    ref = reference()
    reqs, _ = parse(PROMPT, ref)
    wf = next(fn for n, _, fn, _ in MUTATIONS if n == "off-by-one")(ref)
    gap = next(f for f in check(wf, reqs, POL) if f["code"] == "BRANCH_GAP")
    assert gap["witness"]["amount"] == 250000


def test_intervals():
    a = Interval.of(">", 250000).intersect(Interval.of("<", 250001))
    assert not a.empty() and 250000 < a.witness() < 250001
    assert Interval.of(">", 5).intersect(Interval.of("<=", 5)).empty()


def test_diff_names_the_skipped_step_for_a_fast_lane_edit():
    ref = reference()
    fast = next(fn for n, _, fn, _ in MUTATIONS if n == "existing-customer-fastlane")(ref)
    d = explain(ref, fast)
    assert any(c["change"] == "no longer guaranteed" and c["step"] == "Enhanced Screening" and "existing" in c["example"] for c in d)


def test_sample_run_baseline_catches_only_the_path_level_mistakes():
    ref = reference()
    reqs, _ = parse(PROMPT, ref)
    caught = {n for n, _, fn, exp in MUTATIONS if sample_test(fn(ref), reqs) & exp}
    assert caught == {"skip-underwriter-ok", "skip-risk-enhanced", "early-decision-email"}
