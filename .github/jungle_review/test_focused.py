"""focused-v12 contracts. Fake models only: no provider or GitHub calls."""
import json
import unittest

import focused
from provider import Budget

HEAD, BASE = "a" * 40, "b" * 40
APP_PATCH = "@@ -1,3 +1,4 @@\n def save(user):\n-    check(user)\n+    if user:\n+        check(user)\n     store(user)\n@@ -40,2 +41,2 @@\n def other():\n-    return 1\n+    return 2\n"
NEW_PATCH = "@@ -0,0 +1,2 @@\n+def helper():\n+    return True\n"


def inventory(**overrides):
    value = {"repository": "Jungle-Learning/example", "number": 7, "head_sha": HEAD, "base_sha": BASE,
             "title": "t", "description": None, "tree": [],
             "files": [{"path": "app.py", "status": "modified", "patch": APP_PATCH, "changed_lines": [2, 3],
                        "head_content": "def save(user):\n    if user:\n        check(user)\n    store(user)\n"},
                       {"path": "new.py", "status": "added", "patch": NEW_PATCH, "changed_lines": [1, 2],
                        "head_content": "def helper():\n    return True\n"}],
             "context_files": [{"path": "caller.py", "ref": "head", "content": "save(None)", "reason": "caller_or_test"}],
             "coverage": {"complete": True, "notes": [], "info": []}}
    value.update(overrides)
    return value


FINDING = {"path": "app.py", "line": 3, "priority": "P1", "title": "Keep the check for empty users",
           "body": "save(None) now skips check() and stores an unchecked user.", "confidence": 0.95,
           "evidence": ["app.py:R3 guard skips check", "caller.py:1 passes None"],
           "counterargument": "caller.py passes None directly; nothing else checks it."}
HYPOTHESIS = {"path": "app.py", "trigger": "save(None)", "mechanism": "check skipped",
              "check": "base app.py:L2 always ran check(user); head app.py:R2 guards it"}


def answer(findings=(), hypotheses=(), requests=(), gaps=()):
    return {"findings": list(findings), "hypotheses": list(hypotheses), "requests": list(requests),
            "coverage_gaps": list(gaps), "summary": "reviewed"}


class FakeModel:
    """Records every invoke; required_budget prices the envelope by its size."""
    model = "gpt-6.1-sol"
    prices = {"input": 2, "cached": 0.1, "output": 10}

    def __init__(self, answers, cost_per_char=0.2):
        self.answers, self.calls, self.cost_per_char = list(answers), [], cost_per_char

    def required_budget(self, system, payload, phase, *, cache_context=None, instruction=None, output_tokens=4000):
        size = len(json.dumps(payload)) + len(json.dumps(cache_context or {}))
        return int(size * self.cost_per_char) + output_tokens * self.prices["output"]

    def invoke(self, system, payload, budget, phase, *, max_output=7000, ceiling=None, cache_context=None,
               instruction=None, effort="medium", minimum_output=1200):
        self.efforts = getattr(self, "efforts", []) + [effort]
        cost = self.required_budget(system, payload, phase, cache_context=cache_context, output_tokens=minimum_output)
        available = min(budget.remaining, ceiling if ceiling is not None else budget.remaining)
        if cost > available:
            from provider import BudgetExceeded
            raise BudgetExceeded("fake: no room")
        budget.reserve(cost, {"phase": phase})
        self.calls.append({"phase": phase, "payload": payload, "cache_context": cache_context,
                           "instruction": instruction, "ceiling": ceiling, "minimum_output": minimum_output})
        return self.answers.pop(0)


def reads(log=None):
    def read(repository, sha, path, start, end):
        if log is not None:
            log.append((sha, path, start, end))
        return {"path": path, "ref": sha, "start_line": start, "end_line": end, "content": f"{path}@{sha[:1]}"}
    return read


class FocusedTests(unittest.TestCase):
    def test_whole_diff_is_sent_once_and_later_calls_get_only_focused_evidence(self):
        model = FakeModel([answer([FINDING], [HYPOTHESIS], requests=[{"kind": "read", "path": "caller.py", "ref": "head",
                                                                       "start": 1, "end": 5}]),
                           answer([FINDING], [HYPOTHESIS]), answer([FINDING])])
        log = []
        outcome = focused.review_context(inventory(), model, reads(log))
        phases = [c["phase"] for c in model.calls]
        self.assertEqual(phases, ["discovery", "investigation", "verification"])
        discovery_files = model.calls[0]["cache_context"]["files"]
        self.assertTrue(all(f.get("patch") for f in discovery_files))  # every changed file reaches discovery
        for call in model.calls[1:]:
            package = call["cache_context"]
            self.assertNotIn("files", package)                # never the whole diff again
            self.assertEqual([h["path"] for h in package["hunks"]], ["app.py"])
            self.assertNotIn("return 2", json.dumps(package))  # an unrelated hunk of the same file is left out
        self.assertIn((HEAD, "app.py", 1, 43), log)          # head window around the candidate
        self.assertIn((BASE, "app.py", 1, 42), log)          # and the matching base window
        self.assertEqual(outcome["status"], "complete")
        self.assertEqual([f["line"] for f in outcome["findings"]], [3])
        self.assertLessEqual(outcome["budget"]["cost_upper_bound_usd"], 0.50)

    def test_a_hypothesis_without_a_line_gets_every_hunk_of_its_file(self):
        lineless = {"path": "app.py", "trigger": "any save", "mechanism": "behavior change", "check": "unknown"}
        package, notes, _ = focused.focused_package(inventory(), [lineless], reads())
        self.assertIn("return 2", json.dumps(package))
        self.assertEqual(notes, [])

    def test_added_files_drop_duplicate_head_content(self):
        stripped = focused.strip_added_head_content(inventory())
        by_path = {f["path"]: f for f in stripped["files"]}
        self.assertNotIn("head_content", by_path["new.py"])
        self.assertIn("head_content", by_path["app.py"])

    def test_verification_reserve_is_held_back_from_discovery(self):
        model = FakeModel([answer([FINDING]), answer([FINDING])])
        focused.review_context(inventory(), model, reads())
        reserve = focused.estimated_focused_reserve(model, include_investigation=False)
        self.assertLessEqual(model.calls[0]["ceiling"], 500_000 - reserve)
        self.assertEqual(model.calls[0]["minimum_output"], focused.DISCOVERY_MIN_OUTPUT)

    def test_at_most_one_investigation_even_when_it_asks_for_more(self):
        request = {"kind": "read", "path": "caller.py", "ref": "head", "start": 1, "end": 5}
        model = FakeModel([answer([FINDING], requests=[request]), answer([FINDING], requests=[request]),
                           answer([FINDING])])
        focused.review_context(inventory(), model, reads())
        self.assertEqual([c["phase"] for c in model.calls].count("investigation"), 1)

    def test_oversized_diff_withholds_named_patches_and_is_partial(self):
        big = dict(inventory()["files"][0], path="big.py", patch=APP_PATCH + "+x\n" * 20000)
        model = FakeModel([answer([FINDING]), answer([FINDING])], cost_per_char=4)
        current = inventory(files=[*inventory()["files"], big])
        outcome = focused.review_context(current, model, reads(), budget=Budget())
        self.assertEqual(outcome["status"], "partial")
        self.assertTrue(any("big.py" in g and "unreviewed" in g for g in outcome["coverage_gaps"]))
        withheld = [f for f in model.calls[0]["cache_context"]["files"] if f.get("withheld_for_budget")]
        self.assertEqual([f["path"] for f in withheld], ["big.py"])

    def test_documentation_and_tests_are_withheld_before_runtime_code(self):
        current = inventory(files=[*inventory()["files"],
                                   dict(inventory()["files"][0], path="docs/guide.md", patch=APP_PATCH),
                                   dict(inventory()["files"][0], path="tests/test_app.py", patch=APP_PATCH + "+y\n")])
        _, first = focused.discovery_evidence(current, 3)
        _, second = focused.discovery_evidence(current, 4)
        self.assertTrue(any("docs/guide.md" in n for n in first))
        self.assertTrue(any("tests/test_app.py" in n for n in second))
        self.assertFalse(any(n.startswith("app.py:") for n in second))

    def test_unaffordable_review_spends_nothing(self):
        model = FakeModel([], cost_per_char=10_000)
        outcome = focused.review_context(inventory(), model, reads())
        self.assertEqual(outcome["status"], "unavailable")
        self.assertEqual(model.calls, [])
        self.assertEqual(outcome["budget"]["cost_upper_bound_usd"], 0)

    def test_items_that_miss_the_package_are_gaps_not_silent(self):
        many = [dict(HYPOTHESIS, path=f"missing{i}.py", check="x" * 3000) for i in range(30)]
        model = FakeModel([answer([FINDING], many), answer([FINDING])])
        outcome = focused.review_context(inventory(), model, reads())
        self.assertEqual(outcome["status"], "partial")
        self.assertTrue(any("unverified" in g for g in outcome["coverage_gaps"]))

    def test_transport_failure_stops_before_any_further_generation(self):
        def failing(repository, sha, path, start, end):
            return {"path": path, "ref": sha, "error": "read failed (RuntimeError)"}
        model = FakeModel([answer([FINDING])])
        outcome = focused.review_context(inventory(), model, failing)
        self.assertEqual(outcome["status"], "unavailable")
        self.assertTrue(outcome["retryable"])
        self.assertEqual([c["phase"] for c in model.calls], ["discovery"])

    def test_unresolved_verifier_state_cannot_be_complete(self):
        model = FakeModel([answer([FINDING]), answer([], [HYPOTHESIS])])
        self.assertEqual(focused.review_context(inventory(), model, reads())["status"], "partial")
        model = FakeModel([answer([FINDING]), answer([], gaps=["caller of save not shown"])])
        self.assertEqual(focused.review_context(inventory(), model, reads())["status"], "partial")

    def test_incomplete_inventory_is_never_complete(self):
        model = FakeModel([answer([FINDING]), answer([FINDING])])
        current = inventory(coverage={"complete": False, "notes": ["compare API listed at most 300 files"], "info": []})
        self.assertEqual(focused.review_context(current, model, reads())["status"], "partial")

    def test_base_line_mapping_follows_hunk_offsets(self):
        self.assertEqual(focused.base_line_for(APP_PATCH, 1), 1)   # context line
        self.assertEqual(focused.base_line_for(APP_PATCH, 3), 2)   # added line: nearest base line before it
        self.assertEqual(focused.base_line_for(APP_PATCH, 4), 3)   # store(user)
        self.assertEqual(focused.base_line_for(APP_PATCH, 20), 19)  # between hunks: shifted by +1
        self.assertEqual(focused.base_line_for(APP_PATCH, 41), 40)

    def test_empty_context_rows_keep_evidence_and_line_mapping_aligned(self):
        patch = "@@ -1,4 +1,4 @@\n before\n\n-old\n+new\n after\n@@ -10 +10 @@\n-later\n+changed\n"
        first, second = focused.hunks(patch)
        self.assertEqual(first["map"], [(1, 1), (2, 2), (3, None), (None, 3), (4, 4)])
        self.assertIn("R3 +new", first["rows"])
        self.assertIn("R10 +changed", second["rows"])
        self.assertEqual(focused.base_line_for(patch, 4), 4)
        self.assertEqual(focused.head_line_for_base(patch, 4), 4)

    def test_added_line_maps_to_the_base_line_before_it_not_later_deletions(self):
        patch = "@@ -1,5 +1,4 @@\n a\n+x\n b\n-c\n-d\n e\n"
        self.assertEqual(focused.base_line_for(patch, 2), 1)   # +x sits after base line 1 (a)
        self.assertEqual(focused.base_line_for(patch, 3), 2)   # b
        self.assertEqual(focused.base_line_for(patch, 4), 5)   # e, after the two deletions

    def test_a_huge_hunk_is_clipped_around_the_candidate_with_stated_bounds(self):
        patch = "@@ -1,1 +1,3001 @@\n a\n" + "".join(f"+line {i}\n" for i in range(2, 3002))
        hunk = focused.hunks(patch)[0]
        text = focused.hunk_text(hunk, 1500)
        self.assertLessEqual(len(text), focused.HUNK_CLIP_CHARS * 2)
        self.assertIn("clipped around R1500", text)
        self.assertIn("R1500 +line 1500", text)
        self.assertNotIn("R10 +line 10\n", text)
        big = dict(inventory()["files"][0], path="huge.py", patch=patch)
        package, notes, _ = focused.focused_package(inventory(files=[big]), [dict(FINDING, path="huge.py", line=1500)],
                                                    reads())
        self.assertIn("clipped around R1500", json.dumps(package))
        self.assertFalse(any("did not fit" in n for n in notes))

    def test_clipping_keeps_whole_rows_and_states_the_shown_range(self):
        patch = "@@ -1,1 +1,301 @@\n a\n" + "".join(f"+{'w' * 200} {i}\n" for i in range(2, 302))
        hunk = focused.hunks(patch)[0]
        text = focused.hunk_text(hunk, 150)
        self.assertLessEqual(len(text), focused.HUNK_CLIP_CHARS * 2)
        header, *rows = text.split("\n")
        shown = [int(r.split(" ")[0][1:]) for r in rows]
        self.assertTrue(all(r.endswith(f" {n}") for r, n in zip(rows, shown)))  # no row cut mid-line
        self.assertIn(f"showing R{shown[0]}-R{shown[-1]} ", header)       # header names exactly what is shown
        self.assertIn(150, shown)
        self.assertLess(len(shown), 121)

    def test_a_single_overlong_row_is_marked_truncated(self):
        patch = "@@ -1,1 +1,2 @@\n a\n+" + "q" * 40000 + "\n"
        text = focused.hunk_text(focused.hunks(patch)[0], 2)
        self.assertIn("[row truncated at the focused-package size cap]", text)
        self.assertLessEqual(len(text), focused.HUNK_CLIP_CHARS * 2)

    def test_package_is_rebuilt_for_paths_the_investigation_adds(self):
        request = {"kind": "read", "path": "caller.py", "ref": "head", "start": 1, "end": 5}
        new_candidate = dict(FINDING, path="new.py", line=2, title="Return the helper's real value")
        model = FakeModel([answer([FINDING], requests=[request]), answer([new_candidate]), answer([FINDING])])
        focused.review_context(inventory(), model, reads())
        verification = model.calls[-1]
        self.assertEqual(verification["phase"], "verification")
        self.assertIn("new.py", [h["path"] for h in verification["cache_context"]["hunks"]])
        self.assertNotIn("new.py", [h["path"] for h in model.calls[1]["cache_context"]["hunks"]])

    def test_largest_patch_is_withheld_first_within_a_category(self):
        small = dict(inventory()["files"][0], path="small.py", patch=APP_PATCH)
        large = dict(inventory()["files"][0], path="large.py", patch=APP_PATCH + "+z\n" * 50)
        _, notes = focused.discovery_evidence(inventory(files=[small, large]), 3)
        self.assertTrue(any(n.startswith("large.py:") for n in notes))
        self.assertFalse(any(n.startswith("small.py:") for n in notes))

    def test_high_variant_propagates_effort_to_every_call_and_reports_it(self):
        request = {"kind": "read", "path": "caller.py", "ref": "head", "start": 1, "end": 5}
        for variant, expected in (("schema-v18", "medium"), ("schema-v18-high", "high")):
            model = FakeModel([answer([FINDING], requests=[request]), answer([FINDING]), answer([FINDING])])
            outcome = focused.review_context(inventory(), model, reads(), variant=variant)
            self.assertEqual(model.efforts, [expected] * 3)
            self.assertEqual([c["phase"] for c in model.calls], ["discovery", "investigation", "verification"])
            for key in ("effort", "discovery_effort", "investigation_effort", "verification_effort"):
                self.assertEqual(outcome[key], expected)

    def test_high_effort_is_sol_only(self):
        model = FakeModel([])
        model.model = "claude-opus-5-5"
        with self.assertRaisesRegex(ValueError, "Sol-only"):
            focused.review_context(inventory(), model, reads(), variant="schema-v18-high")
        self.assertEqual(model.calls, [])

    # -- v13: exact-cost evidence admission and bounded investigation rounds ---------------------

    def big_reads(self, size=10_000):
        def read(repository, sha, path, start, end):
            return {"path": path, "ref": sha, "start_line": start, "end_line": end,
                    "content": "\n".join(f"{path} {start} {i} " + "c" * (size // 40) for i in range(40))}
        return read

    def test_small_diff_admits_more_than_25k_of_requested_evidence_under_the_cap(self):
        requests = [{"kind": "read", "path": f"lib/m{i}.py", "ref": "head", "start": 1, "end": 40} for i in range(4)]
        model = FakeModel([answer([FINDING], requests=requests), answer([FINDING]), answer([FINDING])],
                          cost_per_char=0.3)
        outcome = focused.review_context(inventory(), model, self.big_reads())
        verification = model.calls[-1]
        windows = json.dumps(verification["cache_context"]["windows"])
        self.assertGreater(len(windows), 25_000)
        self.assertTrue(all(f"lib/m{i}.py" in windows for i in range(4)))
        self.assertFalse(any("excluded" in g or "retrieval bound" in g for g in outcome["coverage_gaps"]))

    def test_evidence_is_excluded_only_when_the_final_verification_would_no_longer_fit(self):
        requests = [{"kind": "read", "path": f"lib/m{i}.py", "ref": "head", "start": 1, "end": 40} for i in range(6)]

        class CostlyVerifier(FakeModel):
            """Cheap discovery; verification input priced so that only part of the evidence fits."""
            def required_budget(self, system, payload, phase, **kwargs):
                if phase != "verification":
                    return super().required_budget(system, payload, phase, **kwargs)
                size = len(json.dumps(payload)) + len(json.dumps(kwargs.get("cache_context") or {}))
                return int(size * 6) + kwargs.get("output_tokens", 4000) * self.prices["output"]
        model = CostlyVerifier([answer([FINDING], requests=requests), answer([FINDING]), answer([FINDING])],
                               cost_per_char=0.2)
        outcome = focused.review_context(inventory(), model, self.big_reads(11_000), budget=Budget())
        excluded = [g for g in outcome["coverage_gaps"] if "would no longer fit" in g]
        self.assertTrue(excluded)
        self.assertEqual(outcome["status"], "partial")
        self.assertEqual(model.calls[-1]["phase"], "verification")
        self.assertLessEqual(outcome["budget"]["cost_upper_bound_usd"], 0.50)
        # what was admitted really fits: the verification's own preflight passed within the remaining cap
        admitted = json.dumps(model.calls[-1]["cache_context"]["windows"])
        self.assertIn("lib/m0.py", admitted)

    def test_a_window_shared_by_two_candidates_is_sent_once_and_counts_for_both(self):
        first = dict(FINDING, evidence=["caller.py:10 passes None"])
        second = dict(FINDING, title="Another root cause", evidence=["caller.py:12 passes None too"])
        package, notes, _ = focused.focused_package(inventory(), [first, second], reads())
        self.assertFalse(any("no hunk or window" in n for n in notes))
        callers = [w for w in package["windows"] if w["path"] == "caller.py"]
        self.assertEqual(len(callers), 1)

    def test_up_to_three_investigation_rounds_with_cumulative_items_and_no_repeated_requests(self):
        def ask(path):
            return [{"kind": "read", "path": path, "ref": "head", "start": 1, "end": 5}]
        model = FakeModel([answer([FINDING], requests=ask("a.py")),
                           answer([dict(FINDING, title="Second root cause")], requests=ask("b.py") + ask("a.py")),
                           answer([], [HYPOTHESIS], requests=ask("c.py")),
                           answer([], requests=ask("d.py")),
                           answer([FINDING])])
        log = []
        outcome = focused.review_context(inventory(), model, reads(log))
        self.assertEqual([c["phase"] for c in model.calls],
                         ["discovery", "investigation", "investigation", "investigation", "verification"])
        self.assertEqual(outcome["investigation_rounds"], 3)
        self.assertEqual([r["round"] for r in outcome["rounds"]], [1, 2, 3])
        self.assertTrue(all("cost_usd" in r for r in outcome["rounds"]))
        requested = [path for _, path, start, end in log if (start, end) == (1, 5)]
        self.assertEqual(sorted(requested), ["a.py", "b.py", "c.py", "d.py"])  # a.py answered once
        titles = [i.get("title") for i in model.calls[-1]["payload"]["candidates_and_hypotheses"]]
        self.assertIn("Keep the check for empty users", titles)               # carried from discovery
        self.assertIn("Second root cause", titles)                            # added in round 1

    def test_rounds_stop_when_no_new_evidence_arrives(self):
        same = [{"kind": "read", "path": "a.py", "ref": "head", "start": 1, "end": 5}]
        model = FakeModel([answer([FINDING], requests=same), answer([FINDING], requests=same), answer([FINDING])])
        outcome = focused.review_context(inventory(), model, reads())
        self.assertEqual(outcome["investigation_rounds"], 1)

    def test_a_failed_admission_preflight_stops_before_further_generation(self):
        request = {"kind": "read", "path": "a.py", "ref": "head", "start": 1, "end": 5}
        model = FakeModel([answer([FINDING], requests=[request])])
        original = model.required_budget

        def failing(system, payload, phase, **kwargs):
            if phase == "verification":
                from provider import ReviewUnavailable
                raise ReviewUnavailable("Provider HTTP 503")
            return original(system, payload, phase, **kwargs)
        model.required_budget = failing
        outcome = focused.review_context(inventory(), model, reads())
        self.assertEqual(outcome["status"], "unavailable")
        self.assertTrue(outcome["retryable"])
        self.assertEqual([c["phase"] for c in model.calls], ["discovery"])

    # -- v14: malformed keys, error-entry semantics, cumulative request queue, audit descriptors --

    def test_malformed_items_never_crash_and_keyless_items_are_not_merged(self):
        weird = {"path": ["a.py"], "title": {"x": 1}, "trigger": "t"}
        kept, notes = focused.merge_items([weird, dict(weird)], [{"check": "first"}, {"check": "second"}], 40000)
        self.assertEqual(len(kept), 3)          # the duplicate malformed item collapses; both keyless stay
        self.assertEqual(notes, [])
        updated = dict(FINDING, body="newer wording")
        kept, _ = focused.merge_items([updated], [FINDING], 40000)
        self.assertEqual([k["body"] for k in kept], ["newer wording"])  # same path + title: newest wins

    def test_a_malformed_investigation_item_does_not_crash_the_review(self):
        request = {"kind": "read", "path": "caller.py", "ref": "head", "start": 1, "end": 5}
        malformed = {"path": ["app.py"], "title": ["?"], "priority": "P1"}
        model = FakeModel([answer([FINDING], requests=[request]), answer([malformed]), answer([FINDING])])
        outcome = focused.review_context(inventory(), model, reads())
        self.assertIn(outcome["status"], ("complete", "partial"))
        self.assertEqual([c["phase"] for c in model.calls], ["discovery", "investigation", "verification"])
        sent = model.calls[-1]["payload"]["candidates_and_hypotheses"]
        self.assertTrue(any(i.get("path") == ["app.py"] for i in sent))  # kept for the verifier, not dropped
        self.assertTrue(any("no hunk or window" in g for g in outcome["coverage_gaps"]))
        self.assertTrue(any("malformed path or title was kept" in g for g in outcome["coverage_gaps"]))
        self.assertEqual(outcome["status"], "partial")

    def missing_caller_reads(self, also_good=False):
        def read(repository, sha, path, start, end):
            if path == "caller.py" and not (also_good and start > 1):
                return {"path": path, "ref": sha, "error": "not_found"}
            return {"path": path, "ref": sha, "start_line": start, "end_line": end, "content": f"{path}@{sha[:1]}"}
        return read

    def test_missing_denied_and_zero_match_entries_keep_their_meaning(self):
        item = dict(FINDING, evidence=["caller.py:3 passes None"])
        base, entries, _, _ = focused.gather_evidence(inventory(), [item], [
            {"request": {"kind": "search", "path": "app.py", "ref": "head", "query": "nope"},
             "result": {"path": "app.py", "ref": HEAD, "match_count": 0, "windows": []}}], self.missing_caller_reads())
        package = focused.assemble(inventory(), base, entries)
        text = json.dumps(package)
        self.assertIn("not_found", text)                       # the failed read is still a failed read
        self.assertIn('"match_count": 0', text)                # the zero-match search is still evidence
        self.assertFalse(any(w.get("path") is None and "content" in w for w in package["windows"]))
        notes = focused.unresolved_candidate_reads(package, inventory())
        self.assertTrue(any("caller.py:3 head window unavailable (not_found)" in n for n in notes))

    def test_a_failed_candidate_read_is_resolved_only_by_an_admitted_window_of_that_file(self):
        first = dict(FINDING, evidence=["caller.py:1 passes None"])
        second = dict(FINDING, title="Another", evidence=["caller.py:90 passes None"])
        base, entries, _, _ = focused.gather_evidence(inventory(), [first, second], [],
                                                      self.missing_caller_reads(also_good=True))
        package = focused.assemble(inventory(), base, entries)
        self.assertIn("not_found", json.dumps(package))
        self.assertEqual(focused.unresolved_candidate_reads(package, inventory()), [])
        model = FakeModel([answer([dict(FINDING, evidence=["caller.py:3 passes None"])]), answer([FINDING])])
        outcome = focused.review_context(inventory(), model, self.missing_caller_reads())
        self.assertEqual(outcome["status"], "partial")
        self.assertTrue(any("window unavailable (not_found)" in g for g in outcome["coverage_gaps"]))

    def test_requests_are_queued_cumulatively_without_duplicates(self):
        def ask(*paths):
            return [{"kind": "read", "path": p, "ref": "head", "start": 1, "end": 5} for p in paths]
        model = FakeModel([answer([FINDING], requests=ask(*[f"r{i}.py" for i in range(8)])),
                           answer([FINDING], requests=ask("r7.py", "r1.py", "new.py")),
                           answer([FINDING]), answer([FINDING])])
        log = []
        outcome = focused.review_context(inventory(), model, reads(log))
        answered = [path for _, path, start, end in log if (start, end) == (1, 5)]
        self.assertEqual(sorted(answered), sorted([f"r{i}.py" for i in range(8)] + ["new.py"]))  # each once
        self.assertEqual(len(answered), len(set(answered)))
        # new.py (new evidence) earns a second round; r7/r1 were already answered and are not re-asked
        self.assertEqual([c["phase"] for c in model.calls], ["discovery", "investigation", "investigation",
                                                             "verification"])
        self.assertEqual(outcome["rounds"][0]["requests_answered_before"], 8)  # all 8 before round 1
        self.assertEqual(outcome["rounds"][1]["requests_answered_before"], 1)  # only new.py
        self.assertFalse(any("still unanswered" in g for g in outcome["coverage_gaps"]))

    def test_audit_describes_admitted_and_excluded_evidence_without_content(self):
        request = {"kind": "read", "path": "lib/x.py", "ref": "head", "start": 10, "end": 14}
        model = FakeModel([answer([dict(FINDING, evidence=["caller.py:3 passes None"])], requests=[request]),
                           answer([FINDING]), answer([FINDING])])
        outcome = focused.review_context(inventory(), model, self.missing_caller_reads())
        admitted = outcome["audit"]["admitted_evidence"]
        self.assertIn({"path": "lib/x.py", "ref": HEAD, "ranges": [[10, 10]], "truncated": False}, admitted)
        self.assertTrue(any(d.get("error") == "not_found" and d.get("candidate") == 1 for d in admitted))
        self.assertIn("excluded_evidence", outcome["audit"])
        self.assertTrue(all("content" not in d for d in admitted))
        self.assertIn("admitted_evidence", outcome["rounds"][0])

    def test_prompt_versions_are_recorded(self):
        model = FakeModel([answer([FINDING]), answer([FINDING])])
        outcome = focused.review_context(inventory(), model, reads())
        self.assertEqual(outcome["experiment_prompt_version"], focused.EXPERIMENT_PROMPT_VERSION)
        self.assertEqual(outcome["version"], focused.VERSION)
        self.assertIn("before/after execution trace", model.calls[0]["instruction"])
        self.assertIn("Never claim you rendered, ran or tested the UI", model.calls[0]["instruction"])
        self.assertIn("infer observable UI behavior", model.calls[0]["instruction"])
        self.assertEqual(focused.EXPERIMENT_PROMPT_VERSION, "schema-v18-prompt-2026-11-12.1")


if __name__ == "__main__":
    unittest.main()
