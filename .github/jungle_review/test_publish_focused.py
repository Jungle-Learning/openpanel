"""The publisher running the real focused pipeline (focused.review_context) with fake models and a fake
GitHub: durable ledger reuse, restored spend, signed prior-finding dedup, summary identity and
content-free diagnostics. No provider or GitHub calls."""
import functools
import json
import re
import unittest

import context as context_module
import focused
import prompts
import publish
import provider
import units
from literal_source import LiteralModel
from publish import Publisher, Target, review_diagnostics, review_pipeline
from test_focused import FINDING, FakeModel, answer, inventory, reads
from test_publish import HEAD, NEWER, REPO, RUN, SIGNER, FakeGitHub, fake_review, genuine_run

FOCUSED_REVIEW = functools.partial(focused.review_context, variant="schema-v18")
READ = {"kind": "read", "path": "caller.py", "ref": "head", "start": 1, "end": 5}


def reservation_records(github, head=HEAD):
    return [s["description"] for s in github.status_store.get(head, []) if " jr1 R " in f" {s['description']}"]


class LedgerFirstModel(FakeModel):
    """A fake model that checks, at each send, that its reservation is already in the status ledger."""

    def __init__(self, answers, github, head=HEAD):
        super().__init__(answers)
        self.github, self.head, self.remaining_at_send, self.ledger_ok = github, head, [], []

    def invoke(self, system, payload, budget, phase, **kwargs):
        self.remaining_at_send.append(budget.remaining)
        result = super().invoke(system, payload, budget, phase, **kwargs)
        self.ledger_ok.append(len(reservation_records(self.github, self.head)) >= len(self.calls))
        return result


def focused_publisher(github, model, head=HEAD, run_id=RUN, attempt=1, logs=None):
    def collect(repository, number, head_sha=None, base_sha=None, **kwargs):
        return inventory(repository=REPO, number=number, head_sha=head_sha, base_sha=github.merge_base)
    return Publisher(github, Target(REPO, 1, head), "claude-opus-5-5", "github-actions", collect, FOCUSED_REVIEW,
                     reads(), run_id=run_id, run_attempt=attempt, signer=SIGNER,
                     model_factory=lambda name: model,
                     log=(logs.append if logs is not None else (lambda *a: None)), sleep=lambda seconds: None)


def summary_body(github):
    return [c["body"] for c in github.comments if "### Jungle Review" in c["body"]][-1]


class FocusedPublisherTests(unittest.TestCase):
    def test_the_entrypoint_wires_the_selected_units_and_literal_source(self):
        collect, review = review_pipeline(context_module, units)
        self.assertIs(collect.func, context_module.collect_context)
        self.assertEqual(collect.keywords, {"max_chars": units.INVENTORY_MAX_CHARS})
        self.assertEqual(units.INVENTORY_MAX_CHARS, 450_000)
        self.assertIs(review.func, units.review_context)
        self.assertEqual(review.keywords, {"variant": publish.REVIEW_VARIANT})
        self.assertEqual(publish.REVIEW_VARIANT, units.EXPERIMENT)
        self.assertIs(publish.Model, LiteralModel)
        self.assertEqual(publish.DEFAULT_MODEL, "gpt-6.1-sol")
        self.assertEqual(publish.REVIEW_EFFORT, "medium")

    def test_a_focused_review_publishes_through_the_durable_ledger(self):
        github, logs = FakeGitHub(), []
        model = LedgerFirstModel([answer([FINDING], requests=[READ]), answer([FINDING]), answer([FINDING])], github)
        self.assertEqual(focused_publisher(github, model, logs=logs).run(), 0)
        self.assertEqual([c["phase"] for c in model.calls], ["discovery", "investigation", "verification"])
        self.assertTrue(model.ledger_ok and all(model.ledger_ok))  # every reservation recorded before its send
        self.assertEqual(len(reservation_records(github)), len(model.calls) + 1)  # runner allowance first
        self.assertLessEqual(github.ledger().spent(), provider.ALL_IN_CAP_MICRODOLLARS)
        self.assertEqual(len(github.posted_reviews), 1)
        comment = github.posted_reviews[0]["comments"][0]
        self.assertRegex(comment["body"], r"<!-- jungle-review:finding v2 fp=[0-9a-f]{24} head=" + HEAD)
        self.assertTrue(github.statuses[-1][2].startswith("Complete"))
        # The summary names what actually ran.
        summary = summary_body(github)
        self.assertIn(f"model `{model.model}`", summary)  # the model that ran, from the result
        self.assertNotIn("claude-opus-5-5", summary)       # not merely the configured one
        self.assertIn(f"effort medium, policy {prompts.VERSION} / {focused.EXPERIMENT_PROMPT_VERSION}", summary)
        self.assertIn(f"publisher {publish.PUBLISHER_VERSION}", summary)

    def test_diagnostics_are_logged_bounded_and_content_free(self):
        github, logs = FakeGitHub(), []
        doc_id = "modal.retries"
        model = LedgerFirstModel([answer([FINDING], requests=[READ, {"kind": "doc", "doc": doc_id},
                                                              {"kind": "doc", "doc": "attacker text @someone"}]),
                                  answer([FINDING]), answer([FINDING])], github)
        focused_publisher(github, model, logs=logs).run()
        lines = [line for line in logs if isinstance(line, str) and line.startswith("Jungle Review diagnostics: ")]
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0].split(": ", 1)[1])
        self.assertEqual(record["status"], "partial")  # the rejected doc ID is a stated gap
        self.assertEqual(record["documentation"]["supplied"], [doc_id])
        self.assertEqual(record["documentation"]["requests"], {"supplied": 1, "rejected": 1})
        self.assertEqual([c["phase"] for c in record["calls"]], ["discovery", "investigation", "verification"])
        for forbidden in (FINDING["title"], FINDING["body"], "attacker", "@someone", "caller.py", "app.py"):
            self.assertNotIn(forbidden, lines[0])
        self.assertLess(len(lines[0]), 4000)

    def test_diagnostics_tolerate_malformed_results(self):
        for result in ({}, {"status": "weird", "documentation": "x", "budget": {"calls": ["x", {"usage": 5}]}},
                       {"evidence_aliases": [1, {"resolved": "a"}], "documentation": {"requests": [None]}}):
            record = review_diagnostics(result)
            self.assertIn(record["status"], ("malformed", "diagnostics_failed"))
        self.assertEqual(review_diagnostics({"documentation": {"supplied": ["not.listed"], "catalog": []}})
                         ["documentation"]["supplied"], [])

    def test_restored_spend_bounds_a_focused_rerun(self):
        github = FakeGitHub()
        # First attempt: an earlier run reserved 50 000 and never settled (it counts in full).
        first = Publisher(github, Target(REPO, 1, HEAD), "claude-opus-5-5", "github-actions",
                          lambda repository, number, head_sha=None, base_sha=None, **kw:
                          {"head_sha": head_sha, "base_sha": github.merge_base, "files": []},
                          fake_review({"status": "unavailable", "findings": [], "coverage_gaps": ["x"]},
                                      reservations=(50_000,), settle=False),
                          lambda *a: {}, run_id=RUN, signer=SIGNER, model_factory=lambda name: object(),
                          log=lambda *a: None, sleep=lambda seconds: None)
        first.run()
        model = LedgerFirstModel([answer([FINDING]), answer([FINDING])], github)
        focused_publisher(github, model, run_id=RUN + 1, attempt=2).run()
        self.assertEqual(model.remaining_at_send[0], 270_000)  # two runner allowances and the old model call
        self.assertLessEqual(github.ledger().spent(), provider.ALL_IN_CAP_MICRODOLLARS)

    def test_a_nearly_spent_scope_sends_nothing_and_is_unavailable(self):
        github = FakeGitHub()
        Publisher(github, Target(REPO, 1, HEAD), "claude-opus-5-5", "github-actions",
                  lambda repository, number, head_sha=None, base_sha=None, **kw:
                  {"head_sha": head_sha, "base_sha": github.merge_base, "files": []},
                  fake_review({"status": "unavailable", "findings": [], "coverage_gaps": ["x"]},
                              reservations=(provider.HARD_CAP_MICRODOLLARS - 5_000,), settle=False),
                  lambda *a: {}, run_id=RUN, signer=SIGNER, model_factory=lambda name: object(),
                  log=lambda *a: None, sleep=lambda seconds: None).run()
        before = len(reservation_records(github))
        model = LedgerFirstModel([answer([FINDING]), answer([FINDING])], github)
        focused_publisher(github, model, run_id=RUN + 1, attempt=2).run()
        self.assertEqual(model.calls, [])
        self.assertEqual(len(reservation_records(github)), before)
        self.assertTrue(github.statuses[-1][2].startswith("Review unavailable"))
        self.assertEqual(github.posted_reviews, [])

    def test_a_signed_prior_finding_is_reused_by_the_focused_verifier_and_not_reposted(self):
        github = FakeGitHub()
        focused_publisher(github, LedgerFirstModel([answer([FINDING]), answer([FINDING])], github)).run()
        prior_fp = re.search(r"fp=([0-9a-f]{24})", github.posted_reviews[0]["comments"][0]["body"]).group(1)
        github.heads = [NEWER]
        github.status_store[NEWER] = []
        github.runs[RUN + 1] = genuine_run(head=NEWER)
        reworded = dict(FINDING, title="Restore the empty-user check before storing", existing_fingerprint=prior_fp)
        model = LedgerFirstModel([answer([reworded], requests=[READ]), answer([reworded]), answer([reworded])],
                                 github, head=NEWER)
        focused_publisher(github, model, head=NEWER, run_id=RUN + 1).run()
        for call in model.calls[1:]:  # investigation and verification both see the signed prior finding
            self.assertEqual([f["fingerprint"] for f in call["cache_context"]["existing_findings"]["findings"]],
                             [prior_fp])
        self.assertEqual(len(github.posted_reviews), 1)  # suppressed as already open
        self.assertTrue(github.statuses[-1][2].startswith("Complete: no new issues; 1 earlier finding(s) still open"))


if __name__ == "__main__":
    unittest.main()
