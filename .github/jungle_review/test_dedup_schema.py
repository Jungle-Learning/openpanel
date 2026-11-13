"""dedup-schema-v2: (1) the system findings schema and the finding parser agree, including existing_fingerprint;
(2) a prior finding's GitHub current_line reaches the verifier; (3) a model-supplied existing_fingerprint is
reused only on the same path and priority at the prior comment's exact current line. Whether two findings
share a root cause stays the model's decision; the host never infers it from titles or nearby lines.
No provider or GitHub calls."""
import json
import re
import unittest

import focused
import prompts
import publish
from review import fingerprint, validated_findings
from test_focused import FINDING, FakeModel, answer, inventory, reads
from test_publish import HEAD, NEWER, RUN, FakeGitHub, genuine_run
from test_publish_focused import LedgerFirstModel, focused_publisher

PRIOR_FP = "2837d8d61ff3aa787a557eb4"


def schema_finding() -> dict:
    match = re.search(r'"findings":\[(\{.*?\})\],\n"requests"', prompts.POLICY, re.S)
    assert match, "system POLICY has no findings schema"
    return json.loads(match.group(1), strict=False)  # the example wraps long values across lines


def context_with(**prior):
    """FINDING sits at app.py:3 (a changed line). The prior comment was signed at line 2 and GitHub now shows
    it at line 3: the focused-v18 canary's shift."""
    record = {"fingerprint": PRIOR_FP, "head": "c" * 40, "path": "app.py", "line": 2, "current_line": 3,
              "priority": "P1", "title": "Keep the check for empty users", "body": "earlier wording"}
    record.update(prior)
    return inventory(existing_findings={"note": "untrusted", "findings": [record]})


def reuse(finding_changes=None, **prior):
    item = dict(FINDING, existing_fingerprint=PRIOR_FP, **(finding_changes or {}))
    accepted, _ = validated_findings({"findings": [item]}, context_with(**prior))
    return accepted[0]


class FindingsSchemaContractTests(unittest.TestCase):
    def test_the_system_schema_lists_exactly_the_fields_the_finding_parser_reads(self):
        self.assertEqual(set(schema_finding()), {"path", "line", "priority", "title", "body", "confidence",
                                                 "evidence", "counterargument", "existing_fingerprint"})

    def test_the_verification_instruction_names_only_schema_and_digest_fields(self):
        self.assertIn("existing_fingerprint", prompts.VERIFY)
        self.assertIn("current_line", prompts.VERIFY)
        named = set(re.findall(r"\b(existing_\w+)\b", prompts.VERIFY)) - {"existing_findings"}
        self.assertLessEqual(named, set(schema_finding()))

    def test_the_verifier_receives_the_schema_field_and_the_current_line(self):
        class Recording(FakeModel):
            def invoke(self, system, payload, budget, phase, **kwargs):
                self.systems = getattr(self, "systems", []) + [system]
                return super().invoke(system, payload, budget, phase, **kwargs)
        model = Recording([answer([FINDING]), answer([FINDING])])
        focused.review_context(context_with(), model, reads())
        self.assertIn('"existing_fingerprint":', model.systems[-1])
        prior = model.calls[-1]["cache_context"]["existing_findings"]["findings"][0]
        self.assertEqual((prior["line"], prior["current_line"]), (2, 3))

    def test_the_digest_keeps_githubs_current_line_apart_from_the_signed_line(self):
        thread = {"body": "**[P1] Keep it**\n\nWhy.\n\nEvidence:\n- x", "line": 3, "original_line": 2}
        digest = publish.finding_digest(thread, PRIOR_FP, HEAD, "app.py", 2)
        self.assertEqual((digest["line"], digest["current_line"]), (2, 3))
        for outdated in (None, True, "3"):
            self.assertIsNone(publish.finding_digest(dict(thread, line=outdated), PRIOR_FP, HEAD, "app.py", 2)
                              ["current_line"])


class TightenedReuseTests(unittest.TestCase):
    def test_reuse_is_accepted_at_the_exact_current_line_with_the_same_path_and_priority(self):
        accepted = reuse({"title": "Restore the empty-user check before storing"})
        self.assertEqual((accepted["fingerprint"], accepted["reuse"]), (PRIOR_FP, "accepted"))

    def test_every_guess_the_old_rule_allowed_is_refused(self):
        for label, finding_changes, prior in (
                ("the old signed line, not the current one", {"line": 2}, {}),
                ("within 25 lines but not the current line", {}, {"current_line": 20, "line": 3}),
                ("same title far away", {"title": "Keep the check for empty users"}, {"current_line": 90}),
                ("outdated comment", {}, {"current_line": None}),
                ("priority changed", {"priority": "P2"}, {}),
                ("other file", {}, {"path": "new.py"}),
                ("boolean current line", {}, {"current_line": True})):
            with self.subTest(label):
                accepted = reuse(finding_changes, **prior)
                self.assertNotEqual(accepted["fingerprint"], PRIOR_FP, label)
                self.assertEqual(accepted["fingerprint"], fingerprint(accepted, context_with(**prior)))
                self.assertEqual(accepted["reuse"], "refused")

    def test_unknown_ids_never_suppress_and_no_id_means_no_reuse(self):
        unknown, _ = validated_findings({"findings": [dict(FINDING, existing_fingerprint="f" * 24)]}, context_with())
        self.assertEqual((unknown[0]["fingerprint"] != PRIOR_FP, unknown[0]["reuse"]), (True, "refused"))
        plain, _ = validated_findings({"findings": [dict(FINDING)]}, context_with())
        self.assertNotIn("reuse", plain[0])
        self.assertEqual(plain[0]["fingerprint"], fingerprint(plain[0], context_with()))

    def test_one_prior_comment_is_reused_by_at_most_one_finding(self):
        second = dict(FINDING, line=2, title="A second claim on the same comment", existing_fingerprint=PRIOR_FP)
        accepted, _ = validated_findings({"findings": [dict(FINDING, existing_fingerprint=PRIOR_FP), second]},
                                         context_with())
        by_line = {f["line"]: f for f in accepted}
        self.assertEqual(by_line[3]["fingerprint"], PRIOR_FP)
        self.assertNotEqual(by_line[2]["fingerprint"], PRIOR_FP)   # not dropped, posted under its own identity
        self.assertEqual(by_line[2]["reuse"], "refused")


class PublisherReplayTests(unittest.TestCase):
    def second_head(self, github):
        github.heads = [NEWER]
        github.status_store[NEWER] = []
        github.runs[RUN + 1] = genuine_run(head=NEWER)

    def published_fingerprint(self, github):
        return re.search(r"fp=([0-9a-f]{24})", github.posted_reviews[0]["comments"][0]["body"]).group(1)

    def test_a_shifted_open_finding_reused_at_its_current_line_is_not_reposted(self):
        github = FakeGitHub()
        focused_publisher(github, LedgerFirstModel([answer([FINDING]), answer([FINDING])], github)).run()
        prior_fp = self.published_fingerprint(github)
        github.threads[0]["line"] = 2           # GitHub now shows the line-3 comment at line 2 on the new head
        self.second_head(github)
        moved = dict(FINDING, line=2, title="Restore the empty-user check", existing_fingerprint=prior_fp)
        logs = []
        model = LedgerFirstModel([answer([moved]), answer([moved])], github, head=NEWER)
        focused_publisher(github, model, head=NEWER, run_id=RUN + 1, logs=logs).run()
        self.assertEqual(len(github.posted_reviews), 1)
        self.assertTrue(github.statuses[-1][2].startswith("Complete: no new issues; 1 earlier finding(s) still open"))
        record = json.loads([line for line in logs if line.startswith("Jungle Review diagnostics: ")][0].split(": ", 1)[1])
        self.assertEqual(record["fingerprint_reuse"], {"accepted": 1, "refused": 0})

    def test_a_reuse_at_the_old_signed_line_is_posted_as_new(self):
        github = FakeGitHub()
        focused_publisher(github, LedgerFirstModel([answer([FINDING]), answer([FINDING])], github)).run()
        prior_fp = self.published_fingerprint(github)
        github.threads[0]["line"] = 2
        self.second_head(github)
        # Still claims line 3, where the comment no longer is (reworded, so its own fingerprint is new).
        stale = dict(FINDING, title="Restore the empty-user check", existing_fingerprint=prior_fp)
        logs = []
        focused_publisher(github, LedgerFirstModel([answer([stale]), answer([stale])], github, head=NEWER),
                          head=NEWER, run_id=RUN + 1, logs=logs).run()
        self.assertEqual(len(github.posted_reviews), 2)
        self.assertTrue(github.statuses[-1][2].startswith("Complete: 1 new advisory finding(s); 1 earlier still open"))
        record = json.loads([line for line in logs if line.startswith("Jungle Review diagnostics: ")][0].split(": ", 1)[1])
        self.assertEqual(record["fingerprint_reuse"], {"accepted": 0, "refused": 1})


class NoImplicitIdentityAcrossHeadsTests(unittest.TestCase):
    def test_the_same_claim_on_a_new_head_is_new_unless_its_reuse_is_accepted(self):
        """v2 pinned an inherited collision: sha256(path:line:title) made the same title at the same line
        number on a later head "already open" without any reuse. v3 binds the claim to its scope, so that
        finding now gets a new identity and is posted (a duplicate, never a silent suppression)."""
        github = FakeGitHub()
        focused_publisher(github, LedgerFirstModel([answer([FINDING]), answer([FINDING])], github)).run()
        github.threads[0]["line"] = 2
        github.heads = [NEWER]
        github.status_store[NEWER] = []
        github.runs[RUN + 1] = genuine_run(head=NEWER)
        focused_publisher(github, LedgerFirstModel([answer([FINDING]), answer([FINDING])], github, head=NEWER),
                          head=NEWER, run_id=RUN + 1).run()
        self.assertEqual(len(github.posted_reviews), 2)


if __name__ == "__main__":
    unittest.main()
