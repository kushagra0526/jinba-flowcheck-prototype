"""The loan-screening example from jinba.io/uses/loan-screening, plus labelled edits.

PROMPT and the node/edge layout are copied from the public page (the diagram URL carries the graph
as JSON). The page does not show edge conditions, field types or model hosting; where the checker
needs them, `published()` fills them with the most literal reading of the prompt and says so.
"""
from copy import deepcopy

PROMPT = ("Create a loan screening workflow for a financial institution. When a loan application is received, "
          "validate the data and enrich with credit information. Route applications based on loan amount—standard "
          "loans go through regular screening, while large loans (>$250k) require enhanced screening. All applications "
          "need risk assessment and compliance checks. If compliance issues are found, request additional documentation. "
          "Every application must be reviewed by an underwriter before final decision. Log everything for regulatory audit.")

FIELDS = {"amount": "number", "applicant_name": "pii", "ssn": "pii", "credit_report": "pii", "loan_id": "text",
          "compliance": ["enum", "ok", "issues"], "customer": ["enum", "new", "existing"]}

ASSUMED = ["edge conditions: route 'amount > 250000' → Enhanced, else → Standard (literal reading of '>$250k')",
           "compliance branch: compliance == 'issues' → Request Documents, else → Underwriter",
           "'Request Documents' emails the applicant and waits for a reply (the page gives no timeout path)",
           "model hosting for the two AI nodes is not shown; read as unspecified"]


def published():
    """The published graph, conditions filled from the prompt (see ASSUMED)."""
    return {"fields": dict(FIELDS), "nodes": {
        "trigger": {"type": "trigger", "label": "Loan Application Received"},
        "validate": {"type": "llm", "label": "Validate & Enrich", "reads": ["applicant_name", "ssn", "credit_report", "amount"]},
        "route": {"type": "condition", "label": "Route by Loan Amount"},
        "standard-screen": {"type": "human", "label": "Standard Screening"},
        "enhanced-screen": {"type": "human", "label": "Enhanced Screening"},
        "risk-check": {"type": "llm", "label": "Risk Assessment", "reads": ["credit_report", "amount"]},
        "compliance": {"type": "condition", "label": "Compliance Check"},
        "additional-docs": {"type": "email", "label": "Request Documents", "reads": ["applicant_name"], "wait": True,
                            "allow_pii": ["applicant_name"]},
        "underwriter": {"type": "human", "label": "Underwriter Review", "decision": True},
        "complete": {"type": "end", "label": "Log & Complete", "log": True}},
        "edges": [
            {"from": "trigger", "to": "validate"}, {"from": "validate", "to": "route"},
            {"from": "route", "to": "enhanced-screen", "when": "amount > 250000"},
            {"from": "route", "to": "standard-screen", "when": "else"},
            {"from": "standard-screen", "to": "risk-check"}, {"from": "enhanced-screen", "to": "risk-check"},
            {"from": "risk-check", "to": "compliance"},
            {"from": "compliance", "to": "additional-docs", "when": "compliance == 'issues'"},
            {"from": "compliance", "to": "underwriter", "when": "else"},
            {"from": "additional-docs", "to": "underwriter"}, {"from": "underwriter", "to": "complete"}]}


def reference():
    """A version that satisfies the prompt and a private-model policy: private models, a timeout path."""
    wf = published()
    for n in ("validate", "risk-check"):
        wf["nodes"][n]["model"] = "private"
    wf["edges"] = [e for e in wf["edges"] if not (e["from"] == "additional-docs")]
    wf["edges"] += [{"from": "additional-docs", "to": "underwriter", "when": "reply"},
                    {"from": "additional-docs", "to": "underwriter", "when": "timeout"}]   # underwriter decides incomplete files
    return wf


def _edge(wf, a, b):
    return next(e for e in wf["edges"] if e["from"] == a and e["to"] == b)


def _m(fn):
    def wrap(wf):
        wf = deepcopy(wf)
        fn(wf)
        return wf
    return wrap


# (name, edit as a chat request, change, expected finding codes)
MUTATIONS = [
    ("off-by-one", "make loans of exactly 250k standard too… (written as two explicit branches)",
     _m(lambda w: (_edge(w, "route", "enhanced-screen").update(when="amount > 250000"),
                   _edge(w, "route", "standard-screen").update(when="amount < 250000"))), {"BRANCH_GAP"}),
    ("overlap", "standard screening up to 300k",
     _m(lambda w: (_edge(w, "route", "enhanced-screen").update(when="amount > 250000"),
                   _edge(w, "route", "standard-screen").update(when="amount <= 300000"))), {"BRANCH_OVERLAP", "MISSING_STEP"}),
    ("skip-underwriter-ok", "if compliance is fine, complete straight away",
     _m(lambda w: _edge(w, "compliance", "underwriter").update(to="complete")), {"MISSING_STEP"}),
    ("skip-risk-enhanced", "enhanced loans go straight to compliance",
     _m(lambda w: _edge(w, "enhanced-screen", "risk-check").update(to="compliance")), {"MISSING_STEP"}),
    ("existing-customer-fastlane", "existing customers skip enhanced screening",
     _m(lambda w: (w["edges"].remove(_edge(w, "route", "enhanced-screen")),
                   w["edges"].insert(2, {"from": "route", "to": "enhanced-screen", "when": "amount > 250000 and customer == 'new'"}))),
     {"MISSING_STEP"}),
    ("external-model", "use GPT for risk scoring",
     _m(lambda w: w["nodes"]["risk-check"].update(model="external")), {"PII_EGRESS"}),
    ("slack-ssn", "post each new application to #loans",
     _m(lambda w: (w["nodes"].__setitem__("notify", {"type": "slack", "label": "Post to #loans", "reads": ["loan_id", "applicant_name", "ssn"]}),
                   _edge(w, "validate", "route").update(to="notify"), w["edges"].append({"from": "notify", "to": "route"}))),
     {"PII_EGRESS"}),
    ("no-final-log", "drop the logging step, the DB is enough",
     _m(lambda w: (_edge(w, "underwriter", "complete").update(to="done"),
                   w["nodes"].__setitem__("done", {"type": "end", "label": "Done"}))), {"AUDIT_GAP", "UNREACHABLE"}),
    ("no-timeout", "remove the incomplete-application path",
     _m(lambda w: w["edges"].remove(next(e for e in w["edges"] if e["from"] == "additional-docs" and e.get("when") == "timeout"))),
     {"UNBOUNDED_WAIT"}),
    ("early-decision-email", "email the applicant the decision after risk assessment",
     _m(lambda w: (w["nodes"].__setitem__("decision-email", {"type": "email", "label": "Email Decision", "decision": True,
                                                             "reads": ["applicant_name"], "allow_pii": ["applicant_name"]}),
                   _edge(w, "risk-check", "compliance").update(to="decision-email"),
                   w["edges"].append({"from": "decision-email", "to": "compliance"}))), {"ORDER_VIOLATION"}),
    ("integer-threshold", "write it as >= 250001 (I first labelled this benign; amounts have cents)",
     _m(lambda w: _edge(w, "route", "enhanced-screen").update(when="amount >= 250001")), {"MISSING_STEP"}),
    ("orphan-step", "add a fraud check (not wired in)",
     _m(lambda w: w["nodes"].__setitem__("fraud", {"type": "llm", "label": "Fraud Check", "model": "private"})),
     {"UNREACHABLE", "DEAD_END"}),
    ("unbounded-loop", "if documents are still missing, ask again",
     _m(lambda w: [e.update(to="compliance") for e in w["edges"] if e["from"] == "additional-docs" and e.get("when") == "reply"]),
     {"CYCLE"}),
]

BENIGN = [
    ("rename", "call it 'Senior Review'", _m(lambda w: w["nodes"]["enhanced-screen"].update(label="Senior Review"))),
    ("explicit-branches", "spell out both branches",
     _m(lambda w: (_edge(w, "route", "enhanced-screen").update(when="amount > 250000"),
                   _edge(w, "route", "standard-screen").update(when="amount <= 250000")))),
    ("slack-no-pii", "post the loan id to #loans",
     _m(lambda w: (w["nodes"].__setitem__("notify", {"type": "slack", "label": "Post to #loans", "reads": ["loan_id"]}),
                   _edge(w, "validate", "route").update(to="notify"), w["edges"].append({"from": "notify", "to": "route"})))),
    ("private-summary", "add an AI summary for the underwriter",
     _m(lambda w: (w["nodes"].__setitem__("summary", {"type": "llm", "label": "Summarize File", "model": "private",
                                                      "reads": ["applicant_name", "credit_report"]}),
                   _edge(w, "compliance", "underwriter").update(to="summary"),
                   w["edges"].append({"from": "summary", "to": "underwriter"})))),
    ("extra-review-1m", "loans over 1M also need a credit committee",
     _m(lambda w: (w["nodes"].__setitem__("committee", {"type": "human", "label": "Credit Committee"}),
                   _edge(w, "enhanced-screen", "risk-check").update(to="committee"),
                   w["edges"].append({"from": "committee", "to": "risk-check"})))),
    ("bounded-retry", "ask for documents up to 2 times",
     _m(lambda w: (w["nodes"]["additional-docs"].update(max_iterations=2),
                   [e.update(to="compliance") for e in w["edges"] if e["from"] == "additional-docs" and e.get("when") == "reply"]))),
    ("extra-log", "also log after screening",
     _m(lambda w: w["nodes"]["risk-check"].update(log=True))),
]
