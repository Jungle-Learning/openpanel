"""dedup-schema-v3: a new finding's identity binds its full validated claim plus the immutable review scope.
A publisher match on an open identity therefore means an explicitly accepted reuse, or the identical claim
on the identical scope. No title- or line-based identity anywhere. No provider or GitHub calls."""
import re
import unittest

import publish
from provider import ReviewUnavailable
from review import CLAIM_FIELDS, fingerprint, validated_findings
from test_focused import answer, inventory
from test_publish import NEWER, RUN, FakeGitHub, genuine_run, own_finding, bound_review, OTHER_MB
from test_publish_focused import LedgerFirstModel, focused_publisher, reservation_records

SCOPE = {"head_sha": "a" * 40, "base_sha": "b" * 40}
CLAIM = {"path": "app.py", "line": 3, "priority": "P1", "title": "Handle missing data",
         "body": "A deleted profile leaves profile None, and save() dereferences it.",
         "evidence": ["app.py:R3 dereferences the profile"], "counterargument": "Nothing checks for None.",
         "confidence": 0.95}
OTHER_MECHANISM = dict(CLAIM, body="An empty 204 response leaves the payload empty, and save() indexes it.",
                       evidence=["app.py:R3 indexes the payload"])


class ClaimIdentityTests(unittest.TestCase):
    def test_incremental_identity_uses_the_pr_merge_base_not_the_checkpoint(self):
        incremental = {**SCOPE, "base_sha": "c" * 40, "merge_base_sha": SCOPE["base_sha"]}
        self.assertEqual(fingerprint(CLAIM, incremental), fingerprint(CLAIM, SCOPE))
        self.assertNotEqual(fingerprint(CLAIM, {**incremental, "merge_base_sha": "d" * 40}),
                            fingerprint(CLAIM, SCOPE))
        with self.assertRaises(ReviewUnavailable):
            fingerprint(CLAIM, {**incremental, "merge_base_sha": None})

    def test_the_identity_is_deterministic_and_binds_every_claim_field_and_the_scope(self):
        identity = fingerprint(CLAIM, SCOPE)
        self.assertRegex(identity, r"^[0-9a-f]{24}$")
        self.assertEqual(fingerprint(dict(CLAIM), dict(SCOPE)), identity)
        changes = {"path": "new.py", "line": 4, "priority": "P2", "title": "Handle missing data!",
                   "body": CLAIM["body"] + " ", "evidence": CLAIM["evidence"] + ["x"], "counterargument": "Other."}
        self.assertEqual(set(changes), set(CLAIM_FIELDS))
        for field, value in changes.items():
            self.assertNotEqual(fingerprint(dict(CLAIM, **{field: value}), SCOPE), identity, field)
        self.assertNotEqual(fingerprint(CLAIM, dict(SCOPE, head_sha="c" * 40)), identity)
        self.assertNotEqual(fingerprint(CLAIM, dict(SCOPE, base_sha="d" * 40)), identity)
        self.assertEqual(fingerprint(dict(CLAIM, confidence=0.91), SCOPE), identity)  # a score, not the claim

    def test_an_unscoped_or_malformed_scope_cannot_produce_an_identity(self):
        for scope in ({}, {"head_sha": "a" * 40}, {"head_sha": "A" * 40, "base_sha": "b" * 40},
                      {"head_sha": "a" * 40 + "\n", "base_sha": "b" * 40}, {"head_sha": "a" * 40, "base_sha": "b" * 40 + "\n"},
                      {"head_sha": "\n" + "a" * 40, "base_sha": "b" * 40}, {"head_sha": "a" * 40 + " ", "base_sha": "b" * 40},
                      {"head_sha": "a" * 39, "base_sha": "b" * 40}, {"head_sha": None, "base_sha": None}):
            with self.assertRaises(ReviewUnavailable):
                fingerprint(CLAIM, scope)

    def test_same_generic_title_and_line_with_different_mechanisms_are_distinct(self):
        accepted, _ = validated_findings({"findings": [CLAIM, OTHER_MECHANISM]}, inventory())
        self.assertEqual(len(accepted), 2)
        self.assertNotEqual(accepted[0]["fingerprint"], accepted[1]["fingerprint"])

    def test_an_identical_claim_twice_in_one_result_is_one_finding(self):
        accepted, _ = validated_findings({"findings": [CLAIM, dict(CLAIM)]}, inventory())
        self.assertEqual(len(accepted), 1)


PRIOR_A, PRIOR_B = "a" * 24, "b" * 24


def with_priors(*fingerprints):
    """Open priors at app.py, priority P1, now shown at line 3 (CLAIM's anchor)."""
    return inventory(existing_findings={"note": "untrusted", "findings": [
        {"fingerprint": fp, "path": "app.py", "line": 2, "current_line": 3, "priority": "P1"} for fp in fingerprints]})


def ids(findings):
    return sorted((f["fingerprint"], f.get("reuse")) for f in findings)


class IdenticalClaimReuseTests(unittest.TestCase):
    def test_an_identical_explicit_reuse_twice_is_one_finding_under_the_prior_id(self):
        reuse = dict(CLAIM, existing_fingerprint=PRIOR_A)
        accepted, _ = validated_findings({"findings": [reuse, dict(reuse)]}, with_priors(PRIOR_A))
        self.assertEqual(ids(accepted), [(PRIOR_A, "accepted")])

    def test_a_later_identical_copy_with_a_valid_unused_reuse_upgrades_the_earlier_record(self):
        for first in (dict(CLAIM), dict(CLAIM, existing_fingerprint="f" * 24)):  # no reuse, then an invalid one
            with self.subTest(first=first.get("existing_fingerprint")):
                accepted, _ = validated_findings(
                    {"findings": [first, dict(CLAIM, existing_fingerprint=PRIOR_A)]}, with_priors(PRIOR_A))
                self.assertEqual(ids(accepted), [(PRIOR_A, "accepted")])

    def test_an_accepted_reuse_is_not_replaced_by_a_later_identical_copy_naming_another_prior(self):
        accepted, _ = validated_findings({"findings": [dict(CLAIM, existing_fingerprint=PRIOR_A),
                                                       dict(CLAIM, existing_fingerprint=PRIOR_B)]},
                                         with_priors(PRIOR_A, PRIOR_B))
        self.assertEqual(ids(accepted), [(PRIOR_A, "accepted")])

    def test_two_distinct_claims_naming_one_prior_stay_separate_and_only_one_reuses_it(self):
        accepted, _ = validated_findings({"findings": [dict(CLAIM, existing_fingerprint=PRIOR_A),
                                                       dict(OTHER_MECHANISM, existing_fingerprint=PRIOR_A)]},
                                         with_priors(PRIOR_A))
        self.assertEqual(len(accepted), 2)
        by_body = {f["body"]: f for f in accepted}
        self.assertEqual((by_body[CLAIM["body"]]["fingerprint"], by_body[CLAIM["body"]]["reuse"]), (PRIOR_A, "accepted"))
        other = by_body[OTHER_MECHANISM["body"]]
        self.assertEqual((other["fingerprint"], other["reuse"]), (fingerprint(other, with_priors()), "refused"))

    def test_an_upgrade_never_takes_a_prior_another_claim_already_holds(self):
        accepted, _ = validated_findings({"findings": [dict(CLAIM),
                                                       dict(OTHER_MECHANISM, existing_fingerprint=PRIOR_A),
                                                       dict(CLAIM, existing_fingerprint=PRIOR_A)]},
                                         with_priors(PRIOR_A))
        by_body = {f["body"]: f for f in accepted}
        self.assertEqual(by_body[OTHER_MECHANISM["body"]]["fingerprint"], PRIOR_A)
        self.assertEqual(by_body[CLAIM["body"]]["fingerprint"], fingerprint(CLAIM, with_priors()))
        self.assertNotIn("reuse", by_body[CLAIM["body"]])


class PriorBodyDigestTests(unittest.TestCase):
    """The verifier decides semantic reuse, so it sees the whole rendered prior body (at most 1600 characters)."""

    def rendered(self, body):
        return publish.finding_text(dict(CLAIM, body=body))

    def test_a_decisive_late_body_survives_and_distinguishes_two_mechanisms(self):
        shared = "The handler reads the profile. " * 25            # ~775 characters in common
        first = shared + "Decisive: a deleted profile leaves it None."
        second = shared + "Decisive: an empty 204 leaves the payload empty."
        self.assertGreater(len(shared), 600)
        digests = [publish.finding_digest({"body": self.rendered(body)}, "1" * 24, "a" * 40) for body in (first, second)]
        self.assertEqual([d["body"] for d in digests], [first, second])
        self.assertNotEqual(digests[0]["body"], digests[1]["body"])

    def test_the_digest_body_stays_bounded_by_the_rendering_limit(self):
        full = "x" * 3000
        rendered = self.rendered(full)
        digest = publish.finding_digest({"body": rendered}, "1" * 24, "a" * 40)
        self.assertEqual(len(digest["body"]), publish.MAX_RENDERED_FINDING_BODY)
        self.assertTrue(digest["body"].endswith("…"))
        oversized = rendered.replace(digest["body"], "y" * 5000)  # not producible by finding_text; still capped
        self.assertLessEqual(len(publish.finding_digest({"body": oversized}, "1" * 24, "a" * 40)["body"]),
                             publish.MAX_RENDERED_FINDING_BODY)


class PublisherIdentityTests(unittest.TestCase):
    def new_head(self, github):
        github.heads = [NEWER]
        github.status_store[NEWER] = []
        github.runs[RUN + 1] = genuine_run(head=NEWER)

    def posted(self, github):
        return [c for review in github.posted_reviews for c in review["comments"]]

    def test_both_mechanisms_publish_in_one_review(self):
        github = FakeGitHub()
        model = LedgerFirstModel([answer([CLAIM, OTHER_MECHANISM]), answer([CLAIM, OTHER_MECHANISM])], github)
        focused_publisher(github, model).run()
        bodies = [c["body"] for c in self.posted(github)]
        self.assertEqual(len(bodies), 2)
        self.assertEqual(len({re.search(r"fp=(\w+)", b).group(1) for b in bodies}), 2)

    def test_a_distinct_bug_with_an_open_findings_title_and_current_line_is_published(self):
        github = FakeGitHub()
        focused_publisher(github, LedgerFirstModel([answer([CLAIM]), answer([CLAIM])], github)).run()
        self.new_head(github)  # the open comment still sits at line 3 on the new head
        focused_publisher(github, LedgerFirstModel([answer([OTHER_MECHANISM]), answer([OTHER_MECHANISM])], github,
                                                   head=NEWER), head=NEWER, run_id=RUN + 1).run()
        self.assertEqual(len(self.posted(github)), 2)
        self.assertTrue(github.statuses[-1][2].startswith("Complete: 1 new advisory finding(s); 1 earlier still open"))

    def test_an_accepted_reuse_at_the_current_anchor_keeps_the_prior_identity(self):
        github = FakeGitHub()
        focused_publisher(github, LedgerFirstModel([answer([CLAIM]), answer([CLAIM])], github)).run()
        prior = re.search(r"fp=(\w+)", self.posted(github)[0]["body"]).group(1)
        self.new_head(github)
        reworded = dict(CLAIM, title="Guard the deleted-profile case", existing_fingerprint=prior)
        focused_publisher(github, LedgerFirstModel([answer([reworded]), answer([reworded])], github, head=NEWER),
                          head=NEWER, run_id=RUN + 1).run()
        self.assertEqual(len(self.posted(github)), 1)
        self.assertTrue(github.statuses[-1][2].startswith("Complete: no new issues; 1 earlier finding(s) still open"))

    def test_an_earlier_signed_identifier_in_the_old_format_is_still_reusable(self):
        legacy = "1" * 24  # any 24-hex signed ID: looked up, never recomputed
        github = FakeGitHub(reviews=[bound_review(base=OTHER_MB)], threads=[own_finding(legacy, base=OTHER_MB, line=3)])
        reuse = dict(CLAIM, existing_fingerprint=legacy)
        focused_publisher(github, LedgerFirstModel([answer([reuse]), answer([reuse])], github)).run()
        self.assertEqual(self.posted(github), [])
        self.assertTrue(github.statuses[-1][2].startswith("Complete: no new issues; 1 earlier finding(s) still open"))

    def test_an_identical_explicit_reuse_twice_publishes_nothing_new(self):
        github = FakeGitHub()
        focused_publisher(github, LedgerFirstModel([answer([CLAIM]), answer([CLAIM])], github)).run()
        prior = re.search(r"fp=(\w+)", self.posted(github)[0]["body"]).group(1)
        self.new_head(github)
        reuse = dict(CLAIM, title="Guard the deleted-profile case", existing_fingerprint=prior)
        focused_publisher(github, LedgerFirstModel([answer([reuse, dict(reuse)]), answer([reuse, dict(reuse)])], github,
                                                   head=NEWER), head=NEWER, run_id=RUN + 1).run()
        self.assertEqual(len(self.posted(github)), 1)
        self.assertTrue(github.statuses[-1][2].startswith("Complete: no new issues; 1 earlier finding(s) still open"))

    def test_a_same_scope_rerun_spends_nothing_and_posts_nothing(self):
        github = FakeGitHub()
        focused_publisher(github, LedgerFirstModel([answer([CLAIM]), answer([CLAIM])], github)).run()
        reserved, spent = len(reservation_records(github)), github.ledger().spent()
        rerun = LedgerFirstModel([answer([CLAIM]), answer([CLAIM])], github)
        focused_publisher(github, rerun, attempt=2).run()
        self.assertEqual((rerun.calls, len(reservation_records(github)), github.ledger().spent()), ([], reserved, spent))
        self.assertEqual(len(github.posted_reviews), 1)


if __name__ == "__main__":
    unittest.main()
