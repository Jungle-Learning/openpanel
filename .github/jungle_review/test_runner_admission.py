"""Per-attempt runner allowance (all-in accounting). Each admitted attempt durably reserves its runner allowance
(an ordinary unsigned reservation, sequence 999) before model setup, context collection or any model call;
attempts that end before admission reserve nothing and are outside the scope's all-in sum. No provider or
GitHub calls."""
import unittest

import provider
from publish import RUNNER_RESERVATION_SEQUENCE, StatusLedger
from test_publish import (EARLIER, HEAD, MB, RUN, SIGNER, FakeGitHub, fake_review, publisher,
                          reserve_status, review_marker)

ALLOWANCE = provider.RUNNER_ALLOWANCE_MICRODOLLARS


def runner_records(github):
    return [d for _, _, d in github.statuses if f".{RUNNER_RESERVATION_SEQUENCE} {ALLOWANCE}" in d and " jr1 R " in d]


class AdmissionTests(unittest.TestCase):
    def test_the_runner_allowance_is_recorded_before_collection_and_any_model_reservation(self):
        github, seen = FakeGitHub(), []

        def collect(repository, number, head_sha=None, base_sha=None, **kwargs):
            seen.append(len(runner_records(github)))
            return {"head_sha": head_sha, "base_sha": github.merge_base, "files": []}
        publisher(github, fake_review({"status": "complete", "findings": []}), collect=collect).run()
        self.assertEqual(seen, [1])                       # already durable when collection starts
        records = [d for _, _, d in github.statuses if " jr1 R " in d]
        self.assertRegex(records[0], rf" jr1 R 1 \w{{12}} {RUN}\.1\.999 {ALLOWANCE}$")
        self.assertRegex(records[1], rf" jr1 R 1 \w{{12}} {RUN}\.1\.1 100000$")

    def test_a_collection_failure_after_admission_keeps_its_runner_allowance(self):
        github = FakeGitHub()

        def collect(*args, **kwargs):
            raise RuntimeError("context source down")
        publisher(github, fake_review({"status": "complete", "findings": []}), collect=collect).run()
        self.assertEqual(github.ledger().spent(), ALLOWANCE)
        self.assertTrue(github.statuses[-1][2].startswith("Review unavailable"))

    def test_attempts_that_end_before_admission_reserve_nothing(self):
        cases = {
            "draft": FakeGitHub(draft=True),
            "already published": None,
            "uncertain earlier write": None,
            "stale event head": FakeGitHub(),
        }
        published = FakeGitHub()
        publisher(published, fake_review({"status": "complete", "findings": []})).run()
        before = len(runner_records(published))
        publisher(published, fake_review({"status": "complete", "findings": []}), attempt=2).run()
        self.assertEqual(len(runner_records(published)), before)   # re-assert of a published scope
        uncertain = FakeGitHub(statuses=[reserve_status(f"{EARLIER}.1.1", 10)])
        uncertain.status_store[HEAD].insert(0, {"context": "Jungle Review", "state": "pending",
            "description": "Publishing review; jr1 W 1 " + MB[:12] + f" {EARLIER}.1 "
                           + SIGNER.status_sig("Jungle-Learning/example", 1, MB, HEAD, "write", f"{EARLIER}.1")})
        cases["uncertain earlier write"] = uncertain
        for label, github in cases.items():
            if github is None:
                continue
            with self.subTest(label):
                run = publisher(github, fake_review({"status": "complete", "findings": []}),
                                event_head=("b" * 40 if label == "stale event head" else HEAD))
                run.run()
                self.assertEqual(runner_records(github), [], label)

    def test_a_recovered_published_review_reserves_nothing(self):
        github = FakeGitHub(reviews=[{"body": review_marker(findings=0), "commit": HEAD,
                                      "author_type": "Bot", "author": "github-actions", "editor_type": None,
                                      "editor": None}])
        publisher(github, fake_review({"status": "complete", "findings": []})).run()
        self.assertEqual(runner_records(github), [])

    def test_an_exhausted_scope_refuses_before_reserving(self):
        # An earlier attempt reserved its allowance and nearly all of the model cap, then crashed unsettled.
        github = FakeGitHub(statuses=[reserve_status(f"{EARLIER}.1.999", ALLOWANCE),
                                      reserve_status(f"{EARLIER}.1.1", provider.HARD_CAP_MICRODOLLARS - 10_000)])
        calls = fake_review({"status": "complete", "findings": []})
        publisher(github, calls).run()
        self.assertEqual(calls.calls, [])
        self.assertFalse([d for d in runner_records(github) if f"{RUN}.1.999" in d])   # this attempt reserved nothing
        self.assertIn("Review budget exhausted", github.statuses[-1][2])

    def test_each_admitted_attempt_reserves_its_own_allowance(self):
        github, seen = FakeGitHub(), []
        publisher(github, fake_review({"status": "unavailable", "findings": [], "coverage_gaps": ["x"]},
                                      reservations=(100_000,), settle=False)).run()

        def retry(context, model, read_file, budget):
            seen.append(budget.remaining)
            return {"status": "unavailable", "findings": [], "coverage_gaps": ["y"]}
        publisher(github, retry, run_id=RUN + 1, attempt=2).run()
        self.assertEqual(seen, [provider.ALL_IN_CAP_MICRODOLLARS - 2 * ALLOWANCE - 100_000])
        self.assertEqual(len(runner_records(github)), 2)
        self.assertEqual(github.ledger().runner_reserved(), 2 * ALLOWANCE)

    def test_reports_keep_model_and_runner_money_apart_on_a_later_attempt(self):
        github, reports = FakeGitHub(), []
        publisher(github, fake_review({"status": "unavailable", "findings": [], "coverage_gaps": ["x"]},
                                      reservations=(100_000,), settle=False)).run()

        def retry(context, model, read_file, budget):
            budget.reserve(20_000, {"phase": "discovery"})
            reports.append(budget.report())
            return {"status": "unavailable", "findings": [], "coverage_gaps": ["y"]}
        publisher(github, retry, run_id=RUN + 1, attempt=2).run()
        report = reports[0]
        self.assertEqual(report["runner_allowance_usd"], 2 * ALLOWANCE / 1e6)
        self.assertEqual(report["model_cap_usd"], (provider.ALL_IN_CAP_MICRODOLLARS - 2 * ALLOWANCE) / 1e6)  # $0.32
        self.assertEqual(report["cap_usd"], report["model_cap_usd"])
        self.assertEqual(report["cost_upper_bound_usd"], (100_000 + 20_000) / 1e6)      # model only
        self.assertEqual(report["model_cost_upper_bound_usd"], report["cost_upper_bound_usd"])
        self.assertEqual(report["scope_cap_usd"], provider.ALL_IN_CAP_MICRODOLLARS / 1e6)
        self.assertEqual(report["scope_cost_upper_bound_usd"], (2 * ALLOWANCE + 120_000) / 1e6)

    def scope_report(self, statuses):
        github, reports = FakeGitHub(statuses=statuses), []

        def review(context, model, read_file, budget):
            call = budget.reserve(10_000, {"phase": "discovery"})
            budget.settle(call, 5_000, {"input_tokens": 1, "output_tokens": 1})
            reports.append(budget.report())
            return {"status": "unavailable", "findings": [], "coverage_gaps": ["y"]}
        publisher(github, review).run()
        return reports[0]

    def test_an_earlier_unsettled_model_reservation_keeps_scope_billing_incomplete(self):
        report = self.scope_report([reserve_status(f"{EARLIER}.1.1", 40_000)])     # unsigned R, no signed S
        self.assertFalse(report["billing_complete"])
        self.assertEqual(report["prior_unsettled_model_reservations"], 1)
        self.assertTrue(all(call["billing_known"] for call in report["calls"]))   # this attempt itself is settled

    def test_signed_earlier_settlements_and_runner_allowances_keep_scope_billing_complete(self):
        signature = SIGNER.status_sig("Jungle-Learning/example", 1, MB, HEAD, "settle", f"{EARLIER}.1.1|20000")
        settled = {"context": "Jungle Review", "state": "pending",
                   "description": f"Review in progress; jr1 S 1 {MB[:12]} {EARLIER}.1.1 20000 {signature}"}
        report = self.scope_report([settled, reserve_status(f"{EARLIER}.1.1", 40_000),
                                    reserve_status(f"{EARLIER}.1.999", ALLOWANCE)])
        self.assertTrue(report["billing_complete"])
        self.assertEqual(report["prior_unsettled_model_reservations"], 0)

    def test_a_forged_settlement_does_not_complete_earlier_billing(self):
        forged = {"context": "Jungle Review", "state": "pending",
                  "description": f"Review in progress; jr1 S 1 {MB[:12]} {EARLIER}.1.1 20000 {'0' * 32}"}
        report = self.scope_report([forged, reserve_status(f"{EARLIER}.1.1", 40_000)])
        self.assertFalse(report["billing_complete"])

    def test_a_runner_record_that_cannot_be_written_admits_nothing(self):
        github = FakeGitHub(fail_status_after=0)
        calls = fake_review({"status": "complete", "findings": []})
        try:
            publisher(github, calls).run()
        except Exception:  # noqa: BLE001 - the unavailable status itself cannot be written either
            pass
        self.assertEqual(calls.calls, [])

    def test_only_the_runner_sequence_counts_as_runner_allowance(self):
        statuses = [reserve_status("9.1.999", ALLOWANCE), reserve_status("9.1.99", 123),
                    reserve_status("9.1.1", 456), reserve_status("10.2.999", ALLOWANCE)]
        ledger = StatusLedger("Jungle-Learning/example", 1, MB, HEAD, SIGNER, statuses)
        self.assertEqual(ledger.runner_reserved(), 2 * ALLOWANCE)
        self.assertEqual(ledger.spent(), 2 * ALLOWANCE + 123 + 456)

    def test_a_plain_budget_never_carries_more_than_the_model_cap(self):
        with self.assertRaises(ValueError):
            provider.Budget(cap=provider.HARD_CAP_MICRODOLLARS + 1)
        self.assertEqual(provider.Budget().cap, provider.HARD_CAP_MICRODOLLARS)
        self.assertEqual(provider.ALL_IN_CAP_MICRODOLLARS - ALLOWANCE, provider.HARD_CAP_MICRODOLLARS)


if __name__ == "__main__":
    unittest.main()
