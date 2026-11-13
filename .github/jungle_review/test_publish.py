import json
import os
import re
import tempfile
import unittest
from unittest.mock import patch

import publish
import provider
from provider import Budget, BudgetExceeded, Model, ReviewUnavailable
from publish import (DurableBudget, GitHubError, InvalidEvent, MarkerSigner, Publisher, RunVerifier,
                     StatusLedger, Target, load_prior_state, normalize_nodes, parse_event, sanitize,
                     select_model)

REPO = "Jungle-Learning/example"
HEAD = "a" * 40
NEWER = "c" * 40
BASE_TIP = "b" * 40
MB = "d" * 40       # merge base of HEAD with the PR's base
OTHER_MB = "e" * 40  # merge base after a retarget
KEY = "k" * 40
SIGNER = MarkerSigner(KEY)
OTHER_SIGNER = MarkerSigner("z" * 40)
BOT = {"author_type": "Bot", "author": "github-actions", "editor_type": None, "editor": None}
HUMAN = {"author_type": "User", "author": "someone", "editor_type": None, "editor": None}
RUN = 9001       # this run
EARLIER = 9000   # an earlier genuine run for the same PR and head
FORGED = 666
WORKFLOW = {"workflow_id": 77, "path": ".github/workflows/jungle-review.yml", "repository": {"full_name": REPO}}


def genuine_run(head=HEAD, number=1, **fields):
    return {**WORKFLOW, "event": "pull_request_target", "display_title": f"Jungle Review #{number}",
            "head_sha": head, "head_branch": "feature", "pull_requests": [{"number": number}], **fields}


def default_runs():
    return {RUN: genuine_run(), EARLIER: genuine_run(), EARLIER - 1: genuine_run(), RUN + 1: genuine_run(),
            RUN + 2: genuine_run(number=2), RUN + 3: genuine_run()}


def review_marker(head=HEAD, run=EARLIER, outcome="complete", findings=0, signer=SIGNER, number=1, base=MB):
    sig = signer.comment_sig(REPO, number, "review", f"{head}|{base}|{outcome}|{findings}|{run}")
    return (f"<!-- jungle-review:review v2 head={head} base={base} outcome={outcome} findings={findings} "
            f"run={run} sig={sig} -->")


FINDING_TEXT = ("**[P1] Check ownership**\n\nUnowned IDs reach storage.\n\nEvidence:\n- app.py:2\n\n"
                "_Advisory finding from Jungle Review (confidence 0.95)._")


def review_id_for(run):
    return 70_000 + run  # the review a run posted (one per run in these fixtures)


def finding_marker(fp, head=HEAD, run=EARLIER, signer=SIGNER, base=MB, path="app.py", line=2, text=FINDING_TEXT,
                   signed_text=None):
    """A complete v2 finding comment body: rendered text, then its signed marker."""
    code, digest = publish.encode_path(path), publish.text_digest(signed_text if signed_text is not None else text)
    sig = signer.comment_sig(REPO, 1, "finding", publish.finding_payload(fp, head, base, run, code, line, digest))
    return (f"{text}\n\n<!-- jungle-review:finding v2 fp={fp} head={head} base={base} run={run} path={code} "
            f"line={line} text={digest} sig={sig} -->")


def own_finding(fp, run=EARLIER, resolved=False, actor=None, review_id=None, path="app.py", line=2, **marker):
    """An open (or resolved) inline thread holding a finding, inside review `review_id`."""
    return {"resolved": resolved, "body": finding_marker(fp, run=run, path=path, line=line, **marker),
            "path": path, "original_line": line, "line": line,
            "review_id": review_id_for(run) if review_id is None else review_id, **(actor or BOT)}


def bound_review(run=EARLIER, findings=1, head=HEAD, signer=SIGNER, review_id=None, created_at="2026-11-12T03:00:00Z",
                 base=MB, **actor):
    return {"body": review_marker(head=head, run=run, findings=findings, signer=signer, base=base), "commit": head,
            "id": review_id_for(run) if review_id is None else review_id, "created_at": created_at, **BOT, **actor}


def status(description, context="Jungle Review", state="pending"):
    return {"context": context, "description": description, "state": state}


def reserve_status(rid, amount, number=1, base=MB):
    return status(f"Review in progress; jr1 R {number} {base[:12]} {rid} {amount}")


def settle_status(rid, actual, head=HEAD, signer=SIGNER, number=1, signed_for=None, base=MB):
    sig = signer.status_sig(REPO, signed_for or number, base, head, "settle", f"{rid}|{actual}")
    return status(f"Review in progress; jr1 S {number} {base[:12]} {rid} {actual} {sig}")


def published_status(outcome="complete", count=0, head=HEAD, signer=SIGNER, number=1, signed_for=None,
                     still_open=0, base=MB, signed_base=None):
    sig = signer.status_sig(REPO, signed_for or number, signed_base or base, head, "published",
                            f"{outcome}|{count}|{still_open}")
    return status(f"Complete · jr1 P {number} {base[:12]} {outcome} {count} {still_open} {sig}", state="success")


def finding(n=0, fingerprint=None):
    return {"path": "app.py", "line": 2, "priority": "P1", "confidence": 0.95,
            "title": f"Check ownership {n}", "body": "Unowned IDs reach storage.",
            "evidence": ["app.py:2 — storage before guard"], "counterargument": "No guard upstream.",
            "fingerprint": fingerprint or f"{n:024x}"}


class FakeGitHub:
    """One repository; every pull request in it currently has `head` as its head."""

    def __init__(self, head=HEAD, comments=None, reviews=None, threads=None, draft=False,
                 heads_after=None, review_error=None, runs=None, statuses=None, fail_status_after=None,
                 base_refs=None, merge_base=MB, review_rejected=None, review_visible_after=None):
        self.heads = [head] + list(heads_after or [])
        self.reads, self.draft = 0, draft
        self.comments, self.reviews, self.threads = list(comments or []), list(reviews or []), list(threads or [])
        self.posted_reviews, self.writes, self.target_urls = [], [], []
        self.review_error = review_error
        self.runs = default_runs() if runs is None else runs
        self.status_store = {head: list(reversed(statuses or []))}  # newest first, like the API
        self.fail_status_after = fail_status_after
        self.status_posts = 0
        self.review_rejected = review_rejected            # an HTTP status: refused, nothing created
        self.review_visible_after = review_visible_after  # None: visible at once; n: after n listings; -1: never
        self.hidden_review = None
        self.base_refs = list(base_refs or ["main"])
        self.merge_base = merge_base

    @property
    def statuses(self):
        return [(sha, s["state"], s["description"]) for sha, items in self.status_store.items()
                for s in reversed(items)]

    def get_pull(self, repository, number):
        head = self.heads[min(self.reads, len(self.heads) - 1)]
        base_ref = self.base_refs[min(self.reads, len(self.base_refs) - 1)]
        self.reads += 1
        return {"state": "open", "draft": self.draft, "head": {"sha": head}, "base": {"ref": base_ref, "sha": BASE_TIP}}

    def get_merge_base(self, repository, base, head):
        return self.merge_base

    def get_run(self, repository, run_id):
        return self.runs.get(run_id)

    def list_statuses(self, repository, sha):
        return list(self.status_store.get(sha, []))

    def list_issue_comments(self, repository, number):
        return [dict(c) for c in self.comments]

    def list_reviews(self, repository, number):
        if self.hidden_review is not None and self.review_visible_after is not None and self.review_visible_after >= 0:
            self.review_visible_after -= 1
            if self.review_visible_after < 0:
                self._show(*self.hidden_review)
                self.hidden_review = None
        return [dict(r) for r in self.reviews]

    def _show(self, head, body, comments):
        review_id = 90_000 + len(self.reviews)
        self.reviews.append({"body": body, "commit": head, "id": review_id,
                             "created_at": f"2026-11-12T05:{len(self.reviews):02d}:00Z", **BOT})
        self.threads.extend({"resolved": False, "body": c["body"], "path": c["path"], "line": c["line"],
                             "original_line": c["line"], "review_id": review_id, **BOT} for c in comments)

    def list_review_threads(self, repository, number):
        return [dict(t) for t in self.threads]

    def create_comment(self, repository, number, body):
        comment = {"id": 100 + len(self.comments), "url": "https://github.com/x/1#new", "body": body, **BOT}
        self.comments.append(comment)
        self.writes.append("create")
        return {"id": comment["id"], "url": comment["url"]}

    def update_comment(self, repository, comment_id, body):
        for comment in self.comments:
            if comment["id"] == comment_id:
                comment["body"] = body
        self.writes.append("update")
        return {"id": comment_id}

    def create_review(self, repository, number, head, body, comments):
        if self.review_rejected:
            raise GitHubError(f"GitHub HTTP {self.review_rejected}", self.review_rejected)
        self.posted_reviews.append({"head": head, "body": body, "comments": comments})
        if self.review_visible_after is not None:
            self.hidden_review = (head, body, comments)  # accepted, but not listed yet
            raise GitHubError("GitHub transport error: TimeoutError")
        self._show(head, body, comments)
        if self.review_error:
            error, self.review_error = self.review_error, None
            raise error

    def set_status(self, repository, sha, state, description, target_url=None):
        self.status_posts += 1
        if self.fail_status_after is not None and self.status_posts > self.fail_status_after:
            raise GitHubError("GitHub HTTP 500", 500)
        assert len(description) <= 140, description
        self.status_store.setdefault(sha, []).insert(0, status(description, state=state))
        self.target_urls.append(target_url)

    def ledger(self, number=1, head=HEAD, base=MB):
        return StatusLedger(REPO, number, base, head, SIGNER, self.list_statuses(REPO, head))


def fake_review(result, reservations=(100_000,), settle=True):
    calls = []

    def review(context, model, read_file, budget):
        for amount in reservations:
            call = budget.reserve(amount, {"model": "claude-opus-5-5", "effort": "medium", "phase": "x"})
            calls.append(call)
            if settle:
                budget.settle(call, amount // 2, {"input_tokens": 10, "output_tokens": 5})
        return result
    review.calls = calls
    return review


def publisher(github, review=None, collect=None, event_head=HEAD, run_id=RUN, attempt=1, signer=SIGNER,
              number=1):
    collect = collect or (lambda repository, number, head_sha=None, base_sha=None:
                          {"head_sha": head_sha, "base_sha": github.merge_base, "files": []})
    return Publisher(github, Target(REPO, number, event_head), "claude-opus-5-5", "github-actions", collect,
                     review or fake_review({"status": "complete", "findings": [], "coverage_gaps": []}),
                     lambda *a: {}, run_id=run_id, run_attempt=attempt, signer=signer,
                     model_factory=lambda name: object(), log=lambda *a: None,
                     sleep=lambda seconds: sleeps.append(seconds))


sleeps = []


def prior(github, signer=SIGNER):
    return load_prior_state(github, REPO, 1, "github-actions", RunVerifier(github, REPO, RUN, "main"), signer)


def pr_event(action="synchronize", head=HEAD, repo=REPO, number=1, changes=None):
    event = {"action": action, "repository": {"default_branch": "main"},
             "pull_request": {"number": number, "head": {"sha": head}, "base": {"repo": {"full_name": repo}}}}
    if changes is not None:
        event["changes"] = changes
    return event


DISPATCH = {"repository": {"default_branch": "main"}}


class EventTests(unittest.TestCase):
    def test_valid_events(self):
        self.assertEqual(parse_event("pull_request_target", pr_event(), REPO), Target(REPO, 1, HEAD, "main"))
        self.assertEqual(parse_event("workflow_dispatch", {**DISPATCH, "inputs": {"pr_number": "42"}}, REPO),
                         Target(REPO, 42, None, "main"))
        retarget = pr_event(action="edited", changes={"base": {"ref": {"from": "release"}}})
        self.assertEqual(parse_event("pull_request_target", retarget, REPO), Target(REPO, 1, HEAD, "main"))

    def test_invalid_events_are_rejected(self):
        cases = [("pull_request", pr_event()), ("issue_comment", pr_event()),
                 ("pull_request_target", pr_event(action="closed")),
                 ("pull_request_target", pr_event(action="labeled")),
                 ("pull_request_target", pr_event(action="edited")),                         # title/body edit
                 ("pull_request_target", pr_event(action="edited", changes={"title": {"from": "x"}})),
                 ("pull_request_target", pr_event(head="abc")),
                 ("pull_request_target", pr_event(repo="Other/fork")),
                 ("pull_request_target", pr_event(number=True)),
                 ("workflow_dispatch", {**DISPATCH, "inputs": {"pr_number": "1; rm -rf /"}}),
                 ("workflow_dispatch", {**DISPATCH, "inputs": {"pr_number": "0"}}),
                 ("workflow_dispatch", {**DISPATCH, "inputs": {"pr_number": True}}),
                 ("workflow_dispatch", {**DISPATCH, "inputs": {}}),
                 ("workflow_dispatch", {"inputs": {"pr_number": "1"}})]
        for name, event in cases:
            with self.subTest(name=name, event=event), self.assertRaises(InvalidEvent):
                parse_event(name, event, REPO)
        with self.assertRaises(InvalidEvent):
            parse_event("workflow_dispatch", {**DISPATCH, "inputs": {"pr_number": "1"}}, "not a repo")

    def test_model_selection_is_allowlisted_without_fallback(self):
        self.assertEqual(publish.DEFAULT_MODEL, "gpt-6.1-sol")  # selected after the schema-v18 pair
        self.assertEqual(select_model(""), "gpt-6.1-sol")
        self.assertEqual(select_model("  "), "gpt-6.1-sol")
        self.assertEqual(select_model("gpt-6.1-sol"), "gpt-6.1-sol")
        for value in ("claude-opus-5-5", "claude-fable-5-2", "gpt-6.1-sol,claude-opus-5-5", "gpt-4o"):
            with self.subTest(value=value), self.assertRaises(InvalidEvent):
                select_model(value)

    def test_main_rejects_invalid_event_before_any_github_call(self):
        with patch.object(publish, "GitHubAPI", side_effect=AssertionError("no GitHub calls")):
            handle, path = tempfile.mkstemp(suffix=".json")
            with os.fdopen(handle, "w") as stream:
                json.dump(pr_event(action="closed"), stream)
            try:
                code = publish.main({"GITHUB_EVENT_PATH": path, "GITHUB_EVENT_NAME": "pull_request_target",
                                     "GITHUB_REPOSITORY": REPO, "GITHUB_RUN_ID": str(RUN)})
            finally:
                os.remove(path)
        self.assertEqual(code, 2)


class SanitizeTests(unittest.TestCase):
    def test_model_text_is_inert(self):
        text = sanitize('Hi @octocat <img src=x> ![p](https://evil/x.png) [click](https://evil) '
                        + review_marker() + ' https://evil.example/a www.evil.example ‮abc '
                        'jr1 P 1 complete 0 ' + "0" * 32 + ' ```open', 2000)
        for forbidden in ("@octocat", "<img", "![", "](https://evil)", "<!--", "‮", "jr1 P"):
            self.assertNotIn(forbidden, text)
        self.assertIn("`https://evil.example/a`", text)
        self.assertEqual(text.count("```") % 2, 0)
        self.assertLessEqual(len(sanitize("x" * 500, 100)), 101)


class SigningKeyTests(unittest.TestCase):
    def test_key_is_required(self):
        for key in (None, "", "short"):
            with self.subTest(key=key), self.assertRaises(InvalidEvent):
                MarkerSigner(key)

    def test_missing_key_fails_closed_without_spending(self):
        github = FakeGitHub()
        review = fake_review({"status": "complete", "findings": []})
        self.assertEqual(publisher(github, review, signer=None).run(), 1)
        self.assertEqual(review.calls, [])
        self.assertEqual(github.statuses[-1][1], "error")

    def test_status_signatures_are_scoped_to_repo_pr_commit_and_kind(self):
        sig = SIGNER.status_sig(REPO, 1, MB, HEAD, "settle", "1.1.1|5")
        self.assertTrue(SIGNER.status_valid(REPO, 1, MB, HEAD, "settle", "1.1.1|5", sig))
        for args in ((REPO, 1, MB, NEWER, "settle", "1.1.1|5"), (REPO, 2, MB, HEAD, "settle", "1.1.1|5"),
                     (REPO, 1, OTHER_MB, HEAD, "settle", "1.1.1|5"),
                     (REPO, 1, MB, HEAD, "published", "1.1.1|5"), ("Other/repo", 1, MB, HEAD, "settle", "1.1.1|5"),
                     (REPO, 1, MB, HEAD, "settle", "1.1.1|4")):
            with self.subTest(args=args):
                self.assertFalse(SIGNER.status_valid(*args, sig))
        self.assertFalse(OTHER_SIGNER.status_valid(REPO, 1, MB, HEAD, "settle", "1.1.1|5", sig))


class StatusLedgerTests(unittest.TestCase):
    def ledger(self, statuses, number=1, base=MB):
        return StatusLedger(REPO, number, base, HEAD, SIGNER, list(reversed(statuses)))

    def test_unsettled_reservations_count_in_full(self):
        self.assertEqual(self.ledger([reserve_status("1.1.1", 300_000)]).spent(), 300_000)

    def test_signed_settlement_replaces_reservation(self):
        self.assertEqual(self.ledger([reserve_status("1.1.1", 300_000), settle_status("1.1.1", 40_000)]).spent(),
                         40_000)

    def test_charge_above_reservation_counts(self):
        self.assertEqual(self.ledger([reserve_status("1.1.1", 300_000), settle_status("1.1.1", 310_000)]).spent(),
                         310_000)

    def test_forged_records_can_only_add_spend(self):
        statuses = [reserve_status("1.1.1", 300_000),
                    settle_status("1.1.1", 0, signer=OTHER_SIGNER),       # wrong key: ignored
                    settle_status("1.1.1", 0, head=NEWER),                # another commit's signature
                    settle_status("1.1.1", 0, signed_for=2),              # another PR's signature
                    status(f"x; jr1 S 1 {MB[:12]} 1.1.1 0 " + "0" * 32),    # unsigned
                    reserve_status("1.1.1", 5),                            # lower duplicate: max wins
                    reserve_status("666.1.1", 50_000),                    # forged extra: counted
                    reserve_status("7.1.1", 90_000, base=OTHER_MB),        # another base's scope
                    status(f"jr1 R 1 {MB[:12]} 2.1.1 1", context="Other context")]  # other contexts
        self.assertEqual(self.ledger(statuses).spent(), 350_000)

    def test_scopes_are_separate_per_pull_request(self):
        statuses = [reserve_status("1.1.1", 300_000, number=1), reserve_status("2.1.1", 200_000, number=2),
                    published_status(number=2)]
        self.assertEqual(self.ledger(statuses, number=1).spent(), 300_000)
        self.assertIsNone(self.ledger(statuses, number=1).published)
        self.assertEqual(self.ledger(statuses, number=2).spent(), 200_000)
        self.assertEqual(self.ledger(statuses, number=2).published, ("complete", 0, 0))

    def test_published_needs_a_valid_scoped_signature(self):
        self.assertIsNone(self.ledger([published_status(signer=OTHER_SIGNER)]).published)
        self.assertIsNone(self.ledger([published_status(head=NEWER)]).published)
        self.assertIsNone(self.ledger([published_status(number=1, signed_for=2)]).published)
        self.assertEqual(self.ledger([published_status("partial", 2, still_open=3)]).published, ("partial", 2, 3))


class LedgerFlowTests(unittest.TestCase):
    def test_reservation_is_recorded_before_the_request_is_sent(self):
        recorded = []
        budget = DurableBudget(lambda kind, rid, amount: recorded.append((kind, rid, amount)), "9001.1")
        sent = []

        def transport(url, headers, body, timeout=180):
            if url.endswith("count_tokens"):
                return {"input_tokens": 10000}
            sent.append(url)
            self.assertEqual(recorded[-1][0], "R", "the reservation must be durable before generation")
            return {"content": [{"type": "text", "text": "{}"}], "stop_reason": "end_turn",
                    "usage": {"input_tokens": 10000, "output_tokens": 100}}
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}):
            Model("claude-opus-5-5", transport=transport).invoke("s", {}, budget, "discovery")
        self.assertEqual(len(sent), 1)
        self.assertEqual([r[0] for r in recorded], ["R", "S"])
        self.assertEqual(recorded[0][1], "9001.1.1")

    def test_record_failure_sends_nothing(self):
        def fail(kind, rid, amount):
            raise GitHubError("down")
        budget = DurableBudget(fail, "9001.1")
        with self.assertRaises(ReviewUnavailable):
            budget.reserve(1000, {"phase": "x"})
        self.assertEqual(budget.spent, 1000)

    def test_settlement_write_failure_keeps_full_reservation(self):
        github = FakeGitHub(fail_status_after=3)  # runner R ok, pending ok, model R ok, then every write fails
        with self.assertRaises(GitHubError):
            publisher(github, fake_review({"status": "complete", "findings": []}, reservations=(200_000,))).run()
        self.assertEqual(github.ledger().spent(), 200_000 + provider.RUNNER_ALLOWANCE_MICRODOLLARS)

    def test_one_budget_per_scope_across_attempts_and_runs(self):
        github = FakeGitHub()
        publisher(github, fake_review({"status": "unavailable", "findings": [], "coverage_gaps": ["x"]},
                                      reservations=(200_000,), settle=False)).run()
        seen = []

        left = provider.ALL_IN_CAP_MICRODOLLARS - 2 * provider.RUNNER_ALLOWANCE_MICRODOLLARS - 200_000
        second_reservation = left - 50_000   # leaves 50 000

        def second(context, model, read_file, budget):
            seen.append(budget.remaining)
            budget.reserve(second_reservation, {"phase": "y"})
            with self.assertRaises(BudgetExceeded):
                budget.reserve(60_000, {"phase": "z"})
            return {"status": "unavailable", "findings": [], "coverage_gaps": ["y"]}
        publisher(github, second, run_id=RUN + 1, attempt=2).run()
        self.assertEqual(seen, [left])  # each admitted attempt reserved its own runner allowance
        self.assertEqual(github.ledger().spent(), 2 * provider.RUNNER_ALLOWANCE_MICRODOLLARS + 200_000 + second_reservation)

    def test_same_head_in_another_pr_is_its_own_scope_and_reuses_nothing(self):
        github = FakeGitHub()
        publisher(github, fake_review({"status": "complete", "findings": []}, reservations=(400_000,),
                                      settle=False)).run()
        self.assertEqual(github.ledger(number=1).published, ("complete", 0, 0))
        seen = []

        def other_pr(context, model, read_file, budget):
            seen.append(budget.remaining)
            return {"status": "complete", "findings": []}
        publisher(github, other_pr, run_id=RUN + 2, number=2).run()
        self.assertEqual(seen, [provider.HARD_CAP_MICRODOLLARS])  # PR 1's completion and spend are not PR 2's
        self.assertEqual(github.ledger(number=2).published, ("complete", 0, 0))

    def test_ledger_survives_deleted_comments_runs_and_many_heads(self):
        github = FakeGitHub()
        publisher(github, fake_review({"status": "unavailable", "findings": [], "coverage_gaps": ["x"]},
                                      reservations=(200_000,), settle=False)).run()
        github.comments.clear()
        github.runs = {RUN + 1: genuine_run()}
        for index in range(50):  # other commits' activity has no effect on this commit's ledger
            github.status_store[f"{index:040x}"] = [reserve_status("1.1.1", 1)]
        seen = []

        def again(context, model, read_file, budget):
            seen.append(budget.remaining)
            return {"status": "unavailable", "findings": [], "coverage_gaps": ["y"]}
        publisher(github, again, run_id=RUN + 1).run()
        self.assertEqual(seen, [provider.ALL_IN_CAP_MICRODOLLARS - 2 * provider.RUNNER_ALLOWANCE_MICRODOLLARS - 200_000])

    def test_cap_cannot_be_raised(self):
        github = FakeGitHub()
        seen = []
        publisher(github, lambda c, m, r, budget: seen.append((budget.cap, budget.remaining)) or
                  {"status": "complete", "findings": []}).run()
        self.assertEqual(seen, [(provider.ALL_IN_CAP_MICRODOLLARS, provider.HARD_CAP_MICRODOLLARS)])
        with self.assertRaises(ValueError):
            Budget(cap=provider.HARD_CAP_MICRODOLLARS + 1)   # a plain (model) budget never exceeds the model cap
        self.assertEqual(provider.HARD_CAP_MICRODOLLARS, 410_000)  # $0.50 all-in less the $0.09 runner allowance

    def test_ledger_records_hold_only_scope_ids_and_amounts(self):
        github = FakeGitHub()
        publisher(github).run()
        records = [d for _, _, d in github.statuses if "jr1 " in d]
        self.assertEqual(len(records), 4)
        self.assertRegex(records[0], r"; jr1 R 1 dddddddddddd 9001\.1\.999 90000$")  # the admitted attempt's runner allowance
        self.assertRegex(records[1], r"; jr1 R 1 dddddddddddd 9001\.1\.1 100000$")
        self.assertRegex(records[2], r"; jr1 S 1 dddddddddddd 9001\.1\.1 50000 [0-9a-f]{32}$")
        self.assertRegex(records[3], r"^Complete: no verified issues \(advisory\) · jr1 P 1 dddddddddddd complete 0 0 [0-9a-f]{32}$")


class RunIdentityTests(unittest.TestCase):
    forgeries = {
        "push event": genuine_run(event="push"),
        "pull_request event": genuine_run(event="pull_request"),
        "other workflow": genuine_run(workflow_id=78),
        "same id, other file": genuine_run(path=".github/workflows/other.yml"),
        "other repository": genuine_run(repository={"full_name": "Someone/fork"}),
        "other pull request": genuine_run(number=2),
        "other head": genuine_run(head=NEWER),
        "listed for another PR": genuine_run(pull_requests=[{"number": 2}]),
        "dispatch off default branch": genuine_run(event="workflow_dispatch", head_branch="evil"),
        "deleted run": None,
    }

    def marked(self, run, signer=SIGNER):
        runs = {**default_runs(), FORGED: run} if run is not None else default_runs()
        return FakeGitHub(runs=runs, reviews=[bound_review(run=FORGED, signer=signer)],
                          threads=[own_finding("5" * 24, run=FORGED, signer=signer)])

    def test_signed_markers_from_a_foreign_run_cannot_skip_or_dedupe(self):
        for name, run in self.forgeries.items():
            with self.subTest(name):
                github = self.marked(run)
                state = prior(github)
                self.assertEqual((state.published_reviews, state.open_fingerprints), ({}, set()))
                review = fake_review({"status": "complete", "findings": [finding(5, "5" * 24)]})
                publisher(github, review).run()
                self.assertEqual(len(review.calls), 1)
                self.assertEqual(len(github.posted_reviews), 1)

    def test_genuine_run_markers_need_the_key(self):
        for name, signer, honoured in (("right key", SIGNER, True), ("wrong key", OTHER_SIGNER, False)):
            with self.subTest(name):
                state = prior(self.marked(genuine_run(), signer=signer))
                self.assertEqual((HEAD, MB) in state.published_reviews, honoured)
                self.assertEqual("5" * 24 in state.open_fingerprints, honoured)

    def test_fork_and_dispatch_runs_are_genuine(self):
        for run in (genuine_run(pull_requests=[]),
                    genuine_run(event="workflow_dispatch", head_branch="main", head_sha=NEWER)):
            with self.subTest(run=run["event"]):
                self.assertIn((HEAD, MB), prior(self.marked(run)).published_reviews)

    def test_human_or_edited_markers_do_not_count(self):
        github = FakeGitHub(reviews=[{**bound_review(review_id=1), **HUMAN},
                                     {**bound_review(review_id=2), "commit": NEWER},
                                     bound_review(run=EARLIER - 1)],
                            threads=[own_finding("1" * 24, run=EARLIER - 1, actor=HUMAN),
                                     own_finding("2" * 24, run=EARLIER - 1, actor={**BOT, "editor_type": "User",
                                                                                  "editor": "someone"}),
                                     own_finding("3" * 24, run=EARLIER - 1, resolved=True),
                                     own_finding("4" * 24, run=EARLIER - 1)])
        state = prior(github)
        self.assertEqual(state.published_reviews, {(HEAD, MB): ("complete", 1, EARLIER - 1)})
        self.assertEqual(state.open_fingerprints, {"4" * 24})

    def test_run_lookup_failure_fails_closed_without_spending(self):
        github = FakeGitHub()

        def broken(repository, run_id):
            raise GitHubError("GitHub HTTP 502", 502)
        github.get_run = broken
        review = fake_review({"status": "complete", "findings": []})
        with self.assertRaises(GitHubError):
            publisher(github, review).run()
        self.assertEqual(review.calls, [])
        self.assertFalse(any(state == "success" for _, state, _ in github.statuses))

    def test_status_links_to_this_exact_run(self):
        github = FakeGitHub()
        publisher(github).run()
        self.assertEqual(set(github.target_urls), {f"https://github.com/{REPO}/actions/runs/{RUN}"})

    def test_graphql_nodes_normalize_actors(self):
        nodes = normalize_nodes([{"databaseId": 5, "url": "u", "body": "b",
                                  "author": {"__typename": "Bot", "login": "github-actions"},
                                  "editor": {"__typename": "User", "login": "x"}}], "comment")
        self.assertEqual((nodes[0]["author"], nodes[0]["editor_type"]), ("github-actions", "User"))
        # An outdated thread: GitHub reports line null, but the comment keeps its path and original line.
        threads = normalize_nodes([{"isResolved": True, "path": "app.py", "line": None, "comments": {"nodes": [
            {"body": "t", "path": "app.py", "originalLine": 2, "pullRequestReview": {"databaseId": 77},
             "author": {"__typename": "Bot", "login": "github-actions"}, "editor": None}]}}], "thread")
        self.assertEqual(threads[0], {"resolved": True, "body": "t", "path": "app.py", "line": None, "original_line": 2,
                                      "review_id": 77, "author_type": "Bot", "author": "github-actions",
                                      "editor_type": None, "editor": None})
        reviews = normalize_nodes([{"databaseId": 77, "createdAt": "2026-11-12T03:00:00Z", "body": "r",
                                    "commit": {"oid": HEAD}, "author": {"__typename": "Bot", "login": "github-actions"},
                                    "editor": None}], "review")
        self.assertEqual((reviews[0]["id"], reviews[0]["created_at"], reviews[0]["commit"]),
                         (77, "2026-11-12T03:00:00Z", HEAD))


class FindingIntegrityTests(unittest.TestCase):
    """A finding counts only as signed: its text, original path and line, inside its original review."""

    FP = "1" * 24

    def scope_findings(self, *threads, reviews=None):
        # An earlier, published scope of the same head (another merge base), so this run reviews anew.
        github = FakeGitHub(reviews=reviews if reviews is not None else [bound_review(base=OTHER_MB)],
                            threads=list(threads))
        state = prior(github)
        review = fake_review({"status": "complete", "findings": [finding(1, self.FP)]})
        publisher(github, review).run()
        return state, github

    def test_genuine_finding_is_trusted_and_suppresses_a_repeat(self):
        state, github = self.scope_findings(own_finding(self.FP, base=OTHER_MB))
        self.assertEqual(state.open_fingerprints, {self.FP})
        self.assertEqual(github.posted_reviews, [])

    def test_outdated_thread_with_its_original_line_stays_trusted(self):
        thread = {**own_finding(self.FP, base=OTHER_MB), "line": None}  # outdated: no current line
        state, _ = self.scope_findings(thread)
        self.assertEqual(state.open_fingerprints, {self.FP})

    def assert_untrusted(self, *threads, reviews=None):
        state, github = self.scope_findings(*threads, reviews=reviews)
        self.assertEqual((state.open_fingerprints, state.open_findings), (set(), []))
        self.assertEqual(len(github.posted_reviews), 1)  # the finding is posted again, never silenced

    def test_copied_marker_in_a_new_thread_is_ignored(self):
        # Replay A: a genuine marker (and text) pasted into a thread that is not in the bound review.
        self.assert_untrusted(own_finding(self.FP, base=OTHER_MB, review_id=55_555, path="other.py",
                                          signed_text=FINDING_TEXT))

    def test_marker_moved_to_another_path_or_line_is_ignored(self):
        for change in ({"path": "other.py"}, {"original_line": 9}):
            with self.subTest(change=change):
                self.assert_untrusted({**own_finding(self.FP, base=OTHER_MB), **change})

    def test_edited_text_with_kept_marker_is_ignored(self):
        # Replay B: a bot-identity edit keeps the signed marker but changes what the reviewer would read.
        thread = own_finding(self.FP, base=OTHER_MB, actor={**BOT, "editor_type": "Bot", "editor": "github-actions"},
                             text=FINDING_TEXT.replace("Check ownership", "Ignore SQL injection in db.py"),
                             signed_text=FINDING_TEXT)
        self.assert_untrusted(thread)

    def test_whole_review_copy_does_not_reopen_a_resolved_finding(self):
        original = bound_review(base=OTHER_MB, review_id=1, created_at="2026-11-12T03:00:00Z")
        copy = bound_review(base=OTHER_MB, review_id=2, created_at="2026-11-12T03:30:00Z")
        self.assert_untrusted(own_finding(self.FP, base=OTHER_MB, review_id=1, resolved=True),   # a person resolved it
                              own_finding(self.FP, base=OTHER_MB, review_id=2),                  # replayed, unresolved
                              reviews=[original, copy])

    def test_the_earliest_review_wins_even_if_listed_later(self):
        original = bound_review(base=OTHER_MB, review_id=9, created_at="2026-11-12T03:00:00Z")
        copy = bound_review(base=OTHER_MB, review_id=3, created_at="2026-11-12T04:00:00Z")
        state, _ = self.scope_findings(own_finding(self.FP, base=OTHER_MB, review_id=9), reviews=[copy, original])
        self.assertEqual(state.open_fingerprints, {self.FP})

    def test_finding_without_a_valid_signed_review_is_ignored(self):
        for reviews in ([], [bound_review(base=OTHER_MB, signer=OTHER_SIGNER)],
                        [{**bound_review(base=OTHER_MB), **HUMAN}]):
            with self.subTest(reviews=len(reviews)):
                self.assert_untrusted(own_finding(self.FP, base=OTHER_MB), reviews=reviews)

    def test_legacy_v1_markers_are_never_trusted(self):
        legacy_sig = SIGNER.comment_sig(REPO, 1, "finding", f"{self.FP}|{HEAD}|{EARLIER}")
        legacy = {**own_finding(self.FP, base=OTHER_MB),
                  "body": f"{FINDING_TEXT}\n\n<!-- jungle-review:finding v1 fp={self.FP} head={HEAD} "
                          f"run={EARLIER} sig={legacy_sig} -->"}
        self.assert_untrusted(legacy)

    def test_reviewer_digest_uses_the_signed_location_and_text(self):
        state, _ = self.scope_findings(own_finding(self.FP, base=OTHER_MB, path="src/a b.py", line=12))
        self.assertEqual(state.open_findings[0], {"fingerprint": self.FP, "head": HEAD, "path": "src/a b.py",
                                                  "line": 12, "current_line": 12, "priority": "P1",
                                                  "title": "Check ownership",
                                                  "body": "Unowned IDs reach storage."})

    def test_posted_markers_bind_text_path_line_and_review(self):
        github = FakeGitHub()
        publisher(github, fake_review({"status": "complete", "findings": [finding()]})).run()
        body = github.threads[0]["body"]
        self.assertRegex(body, r"<!-- jungle-review:finding v2 fp=[0-9a-f]{24} head=a{40} base=d{40} run=9001 "
                               r"path=YXBwLnB5 line=2 text=[0-9a-f]{64} sig=[0-9a-f]{64} -->$")
        self.assertEqual(prior(github).open_fingerprints, {finding()["fingerprint"]})


class StaleHeadTests(unittest.TestCase):
    def test_stale_event_publishes_nothing_and_spends_nothing(self):
        github = FakeGitHub(head=NEWER)
        review = fake_review({"status": "complete", "findings": []})
        self.assertEqual(publisher(github, review, event_head=HEAD).run(), 0)
        self.assertEqual((github.statuses, github.writes, review.calls), ([], [], []))

    def test_head_moving_during_review_never_claims_success(self):
        # Reads: run, pending-status check, then the head moves before publication.
        github = FakeGitHub(heads_after=[HEAD, NEWER])
        review = fake_review({"status": "complete", "findings": [finding()], "coverage_gaps": []})
        publisher(github, review).run()
        self.assertEqual(github.posted_reviews, [])
        self.assertFalse(any(state == "success" for _, state, _ in github.statuses))
        self.assertEqual(github.ledger().spent(), 50_000 + provider.RUNNER_ALLOWANCE_MICRODOLLARS)  # recorded in the reviewed scope

    def test_draft_is_pending_not_reviewed(self):
        github = FakeGitHub(draft=True)
        review = fake_review({"status": "complete", "findings": []})
        publisher(github, review).run()
        self.assertEqual(review.calls, [])
        self.assertEqual(github.statuses, [(HEAD, "pending", "Draft: review runs when the pull request is ready")])


class DuplicateTests(unittest.TestCase):
    def test_published_scope_is_never_reviewed_again(self):
        github = FakeGitHub()
        publisher(github, fake_review({"status": "complete", "findings": [finding()], "coverage_gaps": []})).run()
        self.assertEqual(len(github.posted_reviews), 1)
        github.comments.clear()
        github.reviews.clear()  # even with the review gone, the signed status record stops spend
        again = fake_review({"status": "complete", "findings": [finding()]})
        publisher(github, again, run_id=RUN + 1).run()
        self.assertEqual((again.calls, len(github.posted_reviews)), ([], 1))
        self.assertTrue(github.statuses[-1][2].startswith(
            "Complete: 1 advisory finding(s) posted · jr1 P 1 dddddddddddd complete 1 0 "))
        self.assertEqual(github.statuses[-1][1], "success")

    def test_forged_published_status_does_not_suppress_review(self):
        github = FakeGitHub(statuses=[published_status(signer=OTHER_SIGNER), published_status(signed_for=2),
                                      status(f"Complete · jr1 P 1 {MB[:12]} complete 0 0 " + "0" * 32, state="success"),
                                      published_status(signed_base=OTHER_MB)])
        review = fake_review({"status": "complete", "findings": []})
        publisher(github, review).run()
        self.assertEqual(len(review.calls), 1)

    def test_ambiguous_review_post_is_not_repeated(self):
        github = FakeGitHub(review_error=GitHubError("timeout", None))
        publisher(github, fake_review({"status": "complete", "findings": [finding()]})).run()
        self.assertEqual(len(github.posted_reviews), 1)
        self.assertEqual(github.ledger().published, ("complete", 1, 0))

    def test_retry_after_crash_finds_signed_review_marker(self):
        github = FakeGitHub(reviews=[{"body": review_marker(findings=2), "commit": HEAD, **BOT}],
                            statuses=[reserve_status(f"{EARLIER}.1.1", 300_000)])
        review = fake_review({"status": "complete", "findings": [finding()]})
        publisher(github, review).run()
        self.assertEqual((review.calls, github.posted_reviews), ([], []))
        self.assertTrue(github.statuses[-1][2].startswith("Complete: 2 advisory finding(s) posted"))

    def test_recovered_summary_never_attributes_the_original_review_to_todays_configuration(self):
        github = FakeGitHub(reviews=[{"body": review_marker(findings=2), "commit": HEAD, **BOT}],
                            statuses=[reserve_status(f"{EARLIER}.1.1", 300_000)])
        review = fake_review({"status": "complete", "findings": [finding()], "model": "gpt-6.1-sol",
                              "effort": "medium", "version": "today"})
        recovering = Publisher(github, Target(REPO, 1, HEAD), "claude-opus-5-5", "github-actions",
                               lambda *a, **k: {}, review, lambda *a: {}, run_id=RUN, signer=SIGNER,
                               model_factory=lambda name: object(), log=lambda *a: None, sleep=lambda s: None)
        recovering.run()  # configured model changed since the original run; nothing is reviewed again
        self.assertEqual(review.calls, [])
        summary = [c["body"] for c in github.comments if "### Jungle Review" in c["body"]][-1]
        self.assertIn(f"those of the original review run {EARLIER}", summary)
        for configured in ("claude-opus-5-5", publish.REVIEW_EFFORT, f"policy {publish.PROMPT_VERSION}"):
            self.assertNotIn(configured, summary)
        self.assertIn(f"publisher {publish.PUBLISHER_VERSION}", summary)

    def test_open_own_fingerprints_are_not_reposted_but_human_ones_do_not_count(self):
        open_fp, human_fp = "1" * 24, "2" * 24
        github = FakeGitHub(reviews=[bound_review(base=OTHER_MB)],
                            threads=[own_finding(open_fp, base=OTHER_MB), own_finding(human_fp, base=OTHER_MB, actor=HUMAN)])
        publisher(github, fake_review({"status": "complete", "findings": [
            finding(1, open_fp), finding(2, human_fp)]})).run()
        bodies = [c["body"] for c in github.posted_reviews[0]["comments"]]
        self.assertEqual(len(bodies), 1)
        self.assertIn(human_fp, bodies[0])

    def test_at_most_five_findings_and_one_review(self):
        github = FakeGitHub()
        publisher(github, fake_review({"status": "complete", "findings": [finding(i) for i in range(9)]})).run()
        self.assertEqual(len(github.posted_reviews), 1)
        self.assertEqual(len(github.posted_reviews[0]["comments"]), 5)
        self.assertTrue(all(c["side"] == "RIGHT" for c in github.posted_reviews[0]["comments"]))

    def test_own_markers_round_trip(self):
        github = FakeGitHub()
        publisher(github, fake_review({"status": "complete", "findings": [finding()]})).run()
        state = prior(github)
        self.assertEqual(state.published_reviews, {(HEAD, MB): ("complete", 1, RUN)})
        self.assertEqual(state.open_fingerprints, {finding()["fingerprint"]})


class PublicationSafetyTests(unittest.TestCase):
    def test_refused_inline_review_publishes_nothing_untracked_and_stays_retryable(self):
        github = FakeGitHub(review_rejected=422)
        publisher(github, fake_review({"status": "complete", "findings": [finding()]})).run()
        self.assertEqual((github.posted_reviews, github.reviews), ([], []))  # no body-only fallback
        self.assertEqual(github.statuses[-1][1], "error")
        self.assertIsNone(github.ledger().published)
        self.assertRegex(github.statuses[-2][2], r"; jr1 X 1 dddddddddddd 9001\.1 [0-9a-f]{32}$")
        # The definite failure releases the write hold: the next run may review again at once.
        github.review_rejected = None
        again = fake_review({"status": "complete", "findings": [finding()]})
        publisher(github, again, run_id=RUN + 1).run()
        self.assertEqual((len(again.calls), len(github.posted_reviews)), (1, 1))

    def test_accepted_write_listed_late_is_found_by_polling_not_reposted(self):
        github = FakeGitHub(review_visible_after=1)  # listed on the second look
        del sleeps[:]
        publisher(github, fake_review({"status": "complete", "findings": [finding()]})).run()
        self.assertEqual(len(github.posted_reviews), 1)
        self.assertEqual(len(github.reviews), 1)
        self.assertEqual(github.ledger().published, ("complete", 1, 0))
        self.assertEqual(sleeps, [2, 5])

    def test_unconfirmed_write_holds_later_runs_until_its_signed_marker_appears(self):
        github = FakeGitHub(review_visible_after=-1)  # accepted by GitHub, never listed in time
        with self.assertRaises(GitHubError):
            publisher(github, fake_review({"status": "complete", "findings": [finding()]})).run()
        self.assertIsNone(github.ledger().published)
        self.assertFalse(any("jr1 X" in d for _, _, d in github.statuses))
        held = fake_review({"status": "complete", "findings": [finding()]})
        self.assertEqual(publisher(github, held, run_id=RUN + 1).run(), 1)
        self.assertEqual((held.calls, len(github.posted_reviews)), ([], 1))
        self.assertTrue(github.statuses[-1][2].startswith("Publication uncertain, not clean"))
        # The accepted review becomes visible: the next run completes it without reviewing again.
        github._show(*github.hidden_review)
        github.hidden_review = None
        recovered = fake_review({"status": "complete", "findings": [finding()]})
        publisher(github, recovered, run_id=RUN + 3).run()
        self.assertEqual((recovered.calls, len(github.posted_reviews)), ([], 1))
        self.assertTrue(github.statuses[-1][2].startswith("Complete: 1 advisory finding(s) posted"))

    def test_old_unresolved_write_holds_its_scope_however_long_ago(self):
        # A signed W from an attempt long ago (the ledger has no time limit) with no X and no
        # marker: every later run, of any age or attempt, spends nothing and posts nothing.
        attempt = f"{EARLIER}.1"
        old_write = status(f"Publishing review; jr1 W 1 {MB[:12]} {attempt} "
                           + SIGNER.status_sig(REPO, 1, MB, HEAD, "write", attempt))
        github = FakeGitHub(statuses=[reserve_status(f"{EARLIER}.1.1", 90_000), old_write])
        for run_id, run_attempt in ((RUN, 1), (RUN + 1, 7), (RUN + 3, 9999)):
            with self.subTest(run=run_id, attempt=run_attempt):
                held = fake_review({"status": "complete", "findings": [finding()]})
                self.assertEqual(publisher(github, held, run_id=run_id, attempt=run_attempt).run(), 1)
                self.assertEqual((held.calls, github.posted_reviews), ([], []))
                self.assertEqual(github.statuses[-1][1], "error")
        self.assertIsNone(github.ledger().published)
        self.assertEqual(github.ledger().spent(), 90_000)
        # A new commit is a new scope and is reviewed normally.
        github.heads, github.status_store[NEWER] = [NEWER], []
        github.runs[RUN + 4] = genuine_run(head=NEWER)
        fresh = fake_review({"status": "complete", "findings": []})
        publisher(github, fresh, event_head=NEWER, run_id=RUN + 4).run()
        self.assertEqual(len(fresh.calls), 1)

    def test_unresolved_write_completes_from_its_signed_review_marker(self):
        attempt = f"{EARLIER}.1"
        old_write = status(f"Publishing review; jr1 W 1 {MB[:12]} {attempt} "
                           + SIGNER.status_sig(REPO, 1, MB, HEAD, "write", attempt))
        github = FakeGitHub(statuses=[old_write],
                            reviews=[bound_review(run=EARLIER)],
                            threads=[own_finding("9" * 24, run=EARLIER)])
        review = fake_review({"status": "complete", "findings": [finding()]})
        publisher(github, review).run()
        self.assertEqual((review.calls, github.posted_reviews), ([], []))
        self.assertEqual(github.ledger().published, ("complete", 1, 0))
        # A forged or wrong-key marker does not release the hold.
        github = FakeGitHub(statuses=[old_write],
                            reviews=[{"body": review_marker(findings=1, run=EARLIER, signer=OTHER_SIGNER),
                                      "commit": HEAD, **BOT}])
        review = fake_review({"status": "complete", "findings": [finding()]})
        self.assertEqual(publisher(github, review).run(), 1)
        self.assertEqual((review.calls, github.posted_reviews), ([], []))

    def test_408_is_ambiguous_not_a_definite_refusal(self):
        github = FakeGitHub(review_rejected=408)
        with self.assertRaises(GitHubError):
            publisher(github, fake_review({"status": "complete", "findings": [finding()]})).run()
        self.assertFalse(any("jr1 X" in d for _, _, d in github.statuses))   # no definite-failure record
        self.assertTrue(any("jr1 W" in d for _, _, d in github.statuses))
        github.review_rejected = None
        held = fake_review({"status": "complete", "findings": [finding()]})
        self.assertEqual(publisher(github, held, run_id=RUN + 1).run(), 1)
        self.assertEqual((held.calls, github.posted_reviews), ([], []))

    def test_definite_refusals_other_than_408_release_the_scope(self):
        for code in (400, 404, 409, 422):
            with self.subTest(code=code):
                github = FakeGitHub(review_rejected=code)
                publisher(github, fake_review({"status": "complete", "findings": [finding()]})).run()
                self.assertTrue(any("jr1 X" in d for _, _, d in github.statuses))
                self.assertFalse(github.ledger().uncertain_write())

    def test_forged_write_records_do_not_hold_publication(self):
        attempt = "9000.1"
        forged = [status(f"Publishing review; jr1 W 1 {MB[:12]} {attempt} " + "0" * 32),
                  status(f"Publishing review; jr1 W 1 {MB[:12]} {attempt} "
                         + OTHER_SIGNER.status_sig(REPO, 1, MB, HEAD, "write", attempt))]
        github = FakeGitHub(statuses=forged)
        review = fake_review({"status": "complete", "findings": []})
        publisher(github, review).run()
        self.assertEqual(len(review.calls), 1)

    def test_prior_open_findings_stay_counted_when_the_model_omits_them(self):
        github = FakeGitHub(reviews=[bound_review(base=OTHER_MB, findings=3)],
                            threads=[own_finding("1" * 24, base=OTHER_MB), own_finding("2" * 24, base=OTHER_MB, resolved=True),
                                     own_finding("3" * 24, base=OTHER_MB, actor=HUMAN)])
        publisher(github, fake_review({"status": "complete", "findings": []})).run()
        self.assertTrue(github.statuses[-1][2].startswith("Complete: no new issues; 1 earlier finding(s) still open"))
        self.assertEqual(github.ledger().published, ("complete", 0, 1))

    def test_new_findings_and_prior_open_findings_are_both_reported(self):
        github = FakeGitHub(reviews=[bound_review(base=OTHER_MB)], threads=[own_finding("1" * 24, base=OTHER_MB)])
        publisher(github, fake_review({"status": "complete", "findings": [finding(7)]})).run()
        self.assertTrue(github.statuses[-1][2].startswith("Complete: 1 new advisory finding(s); 1 earlier still open"))
        self.assertEqual(github.ledger().published, ("complete", 1, 1))

    def test_crash_recovery_keeps_prior_open_count_without_counting_its_own_findings(self):
        recovered_run = EARLIER - 1
        github = FakeGitHub(
            reviews=[bound_review(run=recovered_run), bound_review(run=EARLIER, base=OTHER_MB)],
            threads=[own_finding("1" * 24, run=EARLIER, base=OTHER_MB),   # an earlier scope's open finding
                     own_finding("9" * 24, run=recovered_run)])         # the recovered review's own
        review = fake_review({"status": "complete", "findings": []})
        publisher(github, review).run()
        self.assertEqual(review.calls, [])
        self.assertEqual(github.ledger().published, ("complete", 1, 1))
        self.assertTrue(github.statuses[-1][2].startswith("Complete: 1 new advisory finding(s); 1 earlier still open"))


class RetargetTests(unittest.TestCase):
    def test_retargeted_pr_with_the_same_head_is_a_new_scope(self):
        github = FakeGitHub()
        publisher(github, fake_review({"status": "complete", "findings": [finding()]}, reservations=(400_000,),
                                      settle=False)).run()
        self.assertEqual(github.ledger().published, ("complete", 1, 0))
        github.merge_base, github.base_refs, github.reads = OTHER_MB, ["release"], 0  # retargeted, same head
        seen = []

        def review(context, model, read_file, budget):
            seen.append((budget.remaining, context["base_sha"]))
            return {"status": "complete", "findings": []}
        publisher(github, review, run_id=RUN + 1).run()
        self.assertEqual(seen, [(provider.HARD_CAP_MICRODOLLARS, OTHER_MB)])  # neither old completion nor spend reused
        # The old scope's finding is still an open thread: it stays counted in the new receipt.
        self.assertEqual(github.ledger(base=OTHER_MB).published, ("complete", 0, 1))
        self.assertEqual(github.ledger(base=MB).published, ("complete", 1, 0))

    def test_old_scope_review_marker_does_not_complete_a_retargeted_pr(self):
        github = FakeGitHub(reviews=[{"body": review_marker(findings=1), "commit": HEAD, **BOT}],
                            merge_base=OTHER_MB)
        review = fake_review({"status": "complete", "findings": []})
        publisher(github, review).run()
        self.assertEqual(len(review.calls), 1)

    def test_retarget_during_review_never_claims_success(self):
        # Reads: run, pending-status check, then the base branch changes before publication.
        github = FakeGitHub(base_refs=["main", "main", "release"])
        publisher(github, fake_review({"status": "complete", "findings": [finding()]})).run()
        self.assertEqual(github.posted_reviews, [])
        self.assertFalse(any(state == "success" for _, state, _ in github.statuses))
        self.assertEqual(github.ledger().spent(), 50_000 + provider.RUNNER_ALLOWANCE_MICRODOLLARS)

    def test_context_for_another_merge_base_is_rejected_before_spending(self):
        github = FakeGitHub()
        review = fake_review({"status": "complete", "findings": []})
        collect = lambda repository, number, head_sha=None, base_sha=None: {  # noqa: E731
            "head_sha": head_sha, "base_sha": OTHER_MB, "files": []}
        publisher(github, review, collect=collect).run()
        self.assertEqual(review.calls, [])
        self.assertEqual(github.statuses[-1][1], "error")


class ExistingFindingsTests(unittest.TestCase):
    def test_p0_findings_are_digested(self):
        thread = {"body": "**[P0] Stop the data loss**\n\nAll rows are deleted.\n\nEvidence:\n- x", "path": "a.py",
                  "line": 3}
        digest = publish.finding_digest(thread, "1" * 24, HEAD)
        self.assertEqual((digest["priority"], digest["title"], digest["body"]),
                         ("P0", "Stop the data loss", "All rows are deleted."))

    def test_only_trusted_open_findings_reach_the_reviewer_as_untrusted_evidence(self):
        own_fp, forged_fp, resolved_fp = "1" * 24, "2" * 24, "3" * 24
        github = FakeGitHub()
        publisher(github, fake_review({"status": "complete", "findings": [finding(1, own_fp)]})).run()
        github.threads[0].update(path="app.py", line=2)
        github.threads += [own_finding(forged_fp, signer=OTHER_SIGNER, run=RUN, review_id=github.reviews[0]["id"],
                                       head=HEAD),
                           own_finding(resolved_fp, run=RUN, review_id=github.reviews[0]["id"], resolved=True)]
        seen = []

        def review(context, model, read_file, budget):
            seen.append(context["existing_findings"])
            return {"status": "complete", "findings": [finding(1, own_fp)]}  # same root cause, same fp
        github.heads = [NEWER]
        github.status_store[NEWER] = []
        github.runs[RUN + 1] = genuine_run(head=NEWER)
        publisher(github, review, event_head=NEWER, run_id=RUN + 1).run()
        existing = seen[0]
        self.assertIn("untrusted evidence", existing["note"])
        self.assertEqual([f["fingerprint"] for f in existing["findings"]], [own_fp])
        self.assertEqual(existing["findings"][0], {
            "fingerprint": own_fp, "head": HEAD, "path": "app.py", "line": 2, "current_line": 2, "priority": "P1",
            "title": "Check ownership 1", "body": "Unowned IDs reach storage."})
        self.assertEqual(len(github.posted_reviews), 1)  # the reused fingerprint was suppressed
        self.assertTrue(github.statuses[-1][2].startswith("Complete: no new issues; 1 earlier finding(s) still open"))
        github.reads = 0
        publisher(github, review, event_head=NEWER, run_id=RUN + 1, attempt=2).run()  # re-assert keeps the count
        self.assertEqual(len(seen), 1)
        self.assertTrue(github.statuses[-1][2].startswith("Complete: no new issues; 1 earlier finding(s) still open"))


class OutcomeTests(unittest.TestCase):
    def run_outcome(self, result):
        github = FakeGitHub()
        publisher(github, fake_review(result)).run()
        return github

    def test_summary_lists_only_unexamined_code_and_folds_review_notes(self):
        github = self.run_outcome({"status": "complete", "findings": [], "coverage_gaps": [],
                                   "review_notes": ["Question q1: unresolved source question: which caller?"]})
        body = github.comments[-1]["body"] if hasattr(github, "comments") else None
        if body is None:
            self.skipTest("FakeGitHub keeps no comment bodies")
        head, folded = body.split("<details>", 1)
        self.assertIn("no verified issues", head)
        self.assertNotIn("Question q1", head)
        self.assertIn("Question q1", folded)
        self.assertEqual(github.statuses[-1][1], "success")

    def test_partial_is_an_advisory_success_and_unavailable_never_writes_success(self):
        # A partial review finished: its status is green (advisory), and its signed receipt still says
        # "partial", which automated merge paths do not accept as a complete review.
        github = self.run_outcome({"status": "partial", "findings": [], "coverage_gaps": ["gap"]})
        self.assertEqual(github.statuses[-1][1], "success")
        self.assertIn("Partial coverage", github.statuses[-1][2])
        self.assertIn(" partial 0 0 ", github.statuses[-1][2])
        github = self.run_outcome({"status": "unavailable", "findings": [finding()], "coverage_gaps": ["down"]})
        self.assertNotIn("success", [state for _, state, _ in github.statuses])
        self.assertEqual(github.statuses[-1][1], "error")
        self.assertEqual(github.posted_reviews, [])
        self.assertNotIn("jr1 P", github.statuses[-1][2])  # unavailable may be retried
        self.assertEqual(self.run_outcome({"status": "weird"}).statuses[-1][1], "error")

    def test_context_failure_spends_nothing(self):
        github = FakeGitHub()
        review = fake_review({"status": "complete", "findings": []})

        def collect(repository, number, head_sha=None, base_sha=None):
            raise ValueError("compare failed")
        publisher(github, review, collect=collect).run()
        self.assertEqual(review.calls, [])
        self.assertEqual(github.statuses[-1][1], "error")

    def test_summary_is_one_updatable_comment(self):
        github = FakeGitHub()
        publisher(github).run()
        self.assertEqual(len([c for c in github.comments if publish.SUMMARY_MARKER in c["body"]]), 1)
        self.assertEqual(github.writes.count("create"), 1)


class WorkflowContractTests(unittest.TestCase):
    """Static checks of the privileged workflow that the publisher's trust model relies on."""

    def setUp(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "workflows", "jungle-review.yml")
        with open(path, encoding="utf-8") as handle:
            self.text = handle.read()

    def test_run_name_matches_the_title_the_publisher_verifies(self):
        line = next(l for l in self.text.splitlines() if l.startswith("run-name:"))
        prefix = publish.run_title(1)[:-1]
        self.assertEqual(line, f'run-name: "{prefix}' + '${{ github.event.pull_request.number || inputs.pr_number }}"')

    def test_edited_runs_only_for_a_base_change(self):
        self.assertIn("types: [opened, synchronize, reopened, ready_for_review, edited]", self.text)
        self.assertIn("github.event.action != 'edited' || github.event.changes.base", self.text)

    def test_one_concurrency_group_per_pull_request_for_every_trigger(self):
        self.assertIn("      group: jungle-review-${{ github.event.pull_request.number || inputs.pr_number }}",
                      self.text)
        # Job-level only: a run whose job the condition skips must never join (and so replace a
        # pending review in) the group, as a workflow-level group did on canary PR #2.
        self.assertNotRegex(self.text, r"(?m)^concurrency:")
        self.assertLess(self.text.index("    if: >-"), self.text.index("    concurrency:"))
        self.assertNotRegex(self.text, r"group: .*github\.run_id")

    def test_privileged_job_never_checks_out_or_runs_pull_request_code(self):
        self.assertIn("pull_request_target:", self.text)
        self.assertNotRegex(self.text, r"\n  pull_request:")
        for unsafe in ("head.sha", "head.ref", "head_ref", "gh pr checkout", "npm ", "pip install"):
            self.assertNotIn(unsafe, self.text)
        self.assertIn('fetch --no-tags --depth=1 origin "$GITHUB_SHA"', self.text)
        self.assertIn('test "$(git rev-parse HEAD)" = "$GITHUB_SHA"', self.text)
        self.assertIn('unset GH_TOKEN', self.text)
        self.assertNotRegex(self.text, r"git config .*credential")
        uses = [l for l in self.text.splitlines() if re.match(r"\s*(- )?uses:", l)]
        self.assertEqual(len(uses), 1)
        for line in uses:
            self.assertRegex(line, r"uses: [\w./-]+@[0-9a-f]{40}( #.*)?$")

    def test_permissions_environment_and_secret_mapping(self):
        self.assertIn("permissions: {}", self.text)
        for grant in ("contents: read", "pull-requests: write", "statuses: write", "actions: read"):
            self.assertIn(grant, self.text)
        self.assertNotRegex(self.text, r"(contents|actions): write")
        self.assertIn("environment: jungle-review", self.text)
        self.assertNotIn("deployment: false", self.text)  # rely on ordinary environment protection
        for mapping in ("OPENAI_API_KEY: ${{ secrets.JUNGLE_REVIEW_OPENAI_API_KEY }}",
                        "JUNGLE_REVIEW_MARKER_KEY: ${{ secrets.JUNGLE_REVIEW_MARKER_KEY }}",
                        "JUNGLE_REVIEW_MODEL: ${{ vars.JUNGLE_REVIEW_MODEL }}"):
            self.assertIn(mapping, self.text)
        self.assertNotIn("ANTHROPIC_API_KEY", self.text)
        self.assertNotIn("if: github.actor", self.text)  # no author, bot or Dependabot is skipped


if __name__ == "__main__":
    unittest.main()
