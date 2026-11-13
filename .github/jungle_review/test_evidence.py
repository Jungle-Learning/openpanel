"""evidence-v24: measured evidence repairs M1-M6 on top of units-v23. Fake models and readers only."""
import json
import unittest

import focused
import units
from provider import Budget
from test_focused import answer, inventory
from test_units import HYP, UnitModel, added, modified, record


def numbered_file(total, max_lines=200, max_chars=None, log=None):
    """A read_file fake for a `total`-line file: at most max_lines (and max_chars) per read, with the read's
    own metadata (start_line, end_line, total_lines, truncated), as context.read_context_file returns."""
    def read(repository, sha, path, start, end):
        if log is not None:
            log.append((path, start, end))
        last = min(end, total, start + max_lines - 1)
        rows = [f"line {n} of {path}" for n in range(start, last + 1)]
        truncated = False
        if max_chars is not None:
            kept, used = [], 0
            for row in rows:
                if used + len(row) + 1 > max_chars:
                    truncated = True
                    break
                kept.append(row)
                used += len(row) + 1
            rows = kept
            last = start + len(rows) - 1
        return {"path": path, "ref": sha, "start_line": start, "end_line": last, "total_lines": total,
                "truncated": truncated, "content": "\n".join(rows)}
    return read


def two_file_inventory():
    files = [record("src/a.ts", modified(["a"], ["b"])), record("src/b.ts", modified(["c"], ["d"]))]
    return inventory(files=files, context_files=[])


def run(model, context=None, read_file=None, budget=None):
    return units.review_units(context or two_file_inventory(), model, read_file or numbered_file(1000),
                              budget=budget)


def read_request(path, start, end):
    return {"kind": "read", "path": path, "ref": "head", "start": start, "end": end}


class LineReferenceTests(unittest.TestCase):  # M4
    PATCH = "@@ -10,4 +10,5 @@\n keep\n-old one\n-old two\n+new one\n+new two\n+new three\n tail\n"

    def test_repeated_references_are_all_parsed(self):
        refs = focused.line_references("see src/a.ts:R162,R237 and src/b.ts:R10, 12 then src/c.py:R5")
        self.assertEqual(refs, [("src/a.ts", 162), ("src/a.ts", 237), ("src/b.ts", 10), ("src/b.ts", 12),
                                ("src/c.py", 5)])

    def test_base_references_map_through_the_patch_and_are_never_read_as_head_lines(self):
        patches = {"src/a.ts": self.PATCH}
        # L10 is the context row (head 10); L11 is deleted and maps to the next head row (11).
        self.assertEqual(focused.line_references("src/a.ts:L10,L11", patches), [("src/a.ts", 10), ("src/a.ts", 11)])
        # L20 is after the hunk: it shifts by the hunk's net change (+1).
        self.assertEqual(focused.line_references("src/a.ts:L20", patches), [("src/a.ts", 21)])
        # Mixed sides keep their own meaning; an unprefixed continuation keeps the previous side.
        self.assertEqual(focused.line_references("src/a.ts:L10, R14", patches), [("src/a.ts", 10), ("src/a.ts", 14)])
        # Without a patch an L reference is not guessed.
        self.assertEqual(focused.line_references("src/z.ts:L5"), [])

    def test_references_are_bounded(self):
        text = "src/a.ts:" + ",".join(f"R{n}" for n in range(1, 20))
        self.assertEqual(len(focused.line_references(text)), focused.MAX_PARSED_LINE_REFERENCES)

    def test_a_second_reference_gets_its_own_verification_window(self):
        item = {"path": "src/a.ts", "trigger": "t", "mechanism": "m", "check": "src/a.ts:R1,R400"}
        self.assertEqual([(p, l) for p, l, _ in focused.mentioned(item)],
                         [("src/a.ts", 1), ("src/a.ts", 400)])


class LongDeletionTests(unittest.TestCase):  # M4, root's repro
    def setUp(self):
        deleted = [f"old_value_{n} = compute_something_long_enough_to_make_this_hunk_large({n})" for n in range(1, 301)]
        self.patch = "@@ -1,300 +1,1 @@\n" + "".join(f"-{row}\n" for row in deleted) + "+replacement = 1\n"
        self.inventory = inventory(files=[record("src/a.py", self.patch)], context_files=[])

    def test_an_exact_base_reference_reaches_the_package_through_a_large_deletion(self):
        item = {"path": "src/a.py", "line": 1, "kind": "candidate", "title": "t",
                "evidence": ["src/a.py:L10 — the removed guard"], "body": "b"}
        log = []

        def reader(repository, sha, path, start, end):
            log.append(("base" if sha == self.inventory["base_sha"] else "head", start, end))
            return numbered_file(300)(repository, sha, path, start, end)
        package, _, _ = focused.focused_package(self.inventory, [item], reader)
        hunk_text = package["hunks"][0]["numbered_hunks"]
        self.assertRegex(hunk_text, r"(?m)^L10 -old_value_10 ")      # the exact deleted row is shown
        self.assertIn(("base", 1, 50), log)  # a base window around the exact L10 (R1's own window is separate)

    def test_package_metadata_never_claims_whole_diff_coverage(self):
        item = {"path": "src/a.py", "line": 1, "kind": "candidate", "title": "t", "body": "b"}
        package, _, _ = focused.focused_package(self.inventory, [item], numbered_file(300))
        text = json.dumps(package)
        self.assertNotIn("whole diff was reviewed", text)
        self.assertNotIn("reviewed in discovery", text)
        self.assertIn("may not have been reviewed", package["note"])
        self.assertIn("is not shown here", package["hunks"][0]["numbered_hunks"])  # clipped header, scope-accurate

    def test_mixed_references_keep_their_own_coordinates(self):
        refs = focused.line_references_with_base("src/a.py:L10, R1", {"src/a.py": self.patch})
        self.assertEqual(refs, [("src/a.py", 1, 10), ("src/a.py", 1, None)])


class PagedReadTests(unittest.TestCase):  # M1
    def run_with(self, request, read_file, total_reads=None):
        log = [] if total_reads is None else total_reads
        model = UnitModel([answer(hypotheses=[HYP], requests=[request])] + [answer()] * 8)
        result = run(model, read_file=read_file)
        return result, model

    def statuses(self, result):
        return [s for u in result["units"]["units"] for s in u["read_status"]]

    def test_a_read_beyond_200_lines_is_paged_and_complete(self):
        log = []
        result, model = self.run_with(read_request("src/a.ts", 1, 320), numbered_file(307, log=log))
        status = self.statuses(result)[0]
        self.assertEqual(status["returned"], [1, 307])
        self.assertTrue(status["complete"])
        self.assertEqual([(s, e) for _, s, e in log if _ == "src/a.ts"][:2], [(1, 200), (201, 320)])  # pages asked
        self.assertFalse(any("read of src/a.ts" in g for g in result["coverage_gaps"]))
        followup = [c for c in model.calls if c["phase"] == "investigation"][0]
        self.assertEqual(followup["cache_context"]["read_status"][0]["returned"], [1, 307])

    def test_a_fully_answered_window_is_not_partial(self):
        result, _ = self.run_with(read_request("src/a.ts", 100, 150), numbered_file(1000))
        status = self.statuses(result)[0]
        self.assertTrue(status["complete"])
        self.assertEqual(status["returned"], [100, 150])
        self.assertFalse(any("read of src/a.ts" in g for g in result["coverage_gaps"]))

    def test_a_read_over_the_page_bound_is_a_named_gap_with_metadata_from_the_read(self):
        result, _ = self.run_with(read_request("src/a.ts", 1, 900), numbered_file(1000))
        status = self.statuses(result)[0]
        self.assertFalse(status["complete"])
        self.assertEqual(status["returned"], [1, 600])
        self.assertEqual(status["stopped"], "page bound")
        self.assertTrue(any("read of src/a.ts (head) lines 1-900 returned 1-600 of 1000 (page bound)" in g
                            for g in result["coverage_gaps"]))
        self.assertEqual(result["status"], "partial")

    def test_a_character_truncated_page_continues_from_the_last_line_actually_returned(self):
        log = []
        result, _ = self.run_with(read_request("src/a.ts", 1, 120), numbered_file(1000, max_chars=900, log=log))
        starts = [s for p, s, _ in log if p == "src/a.ts"]
        self.assertEqual(starts[0], 1)
        self.assertGreater(starts[1], 1)  # next page starts after the truncated page's last line
        status = self.statuses(result)[0]
        self.assertEqual(status["requested"], [1, 120])


class SharedAnswerTests(unittest.TestCase):  # C4
    def test_a_request_two_units_make_is_read_once_and_given_to_both(self):
        log = []
        request = read_request("src/shared.ts", 1, 50)
        files = [record("src/a.ts", modified(["a"], ["b"])), record("lib/b.py", modified(["c"], ["d"]))]
        context = inventory(files=files, context_files=[])
        hyp_a, hyp_b = dict(HYP, path="src/a.ts"), dict(HYP, path="lib/b.py")
        model = UnitModel([answer(hypotheses=[hyp_a], requests=[request]),
                           answer(hypotheses=[hyp_b], requests=[request])] + [answer()] * 8)
        routing = units.route(context)
        self.assertEqual(len(routing["units"]), 1)  # small units pack; use two big ones instead
        big_a = record("src/a.ts", added(["x" * 100] * 200), status="added")
        big_b = record("lib/b.py", added(["y" * 100] * 200), status="added")
        context = inventory(files=[big_a, big_b], context_files=[])
        self.assertEqual(len(units.route(context)["units"]), 2)
        result = run(model, context=context, read_file=numbered_file(50, log=log))
        self.assertEqual(sum(1 for p, _, _ in log if p == "src/shared.ts"), 1)
        followups = [c for c in model.calls if c["phase"] == "investigation"]
        self.assertEqual(len(followups), 2)
        for call in followups:
            self.assertTrue(any((e.get("request") or {}).get("path") == "src/shared.ts"
                                for e in call["cache_context"]["answered_requests"]))
        self.assertEqual([u["answered"] for u in result["units"]["units"] if u["audited"]], [1, 1])


class FollowUpRoundTests(unittest.TestCase):  # M2
    def big_two_units(self):
        big_a = record("src/a.ts", added(["x" * 100] * 200), status="added")
        big_b = record("lib/b.py", added(["y" * 100] * 200), status="added")
        return inventory(files=[big_a, big_b], context_files=[])

    def test_second_round_only_with_new_evidence_and_last_round_requests_reach_verification(self):
        first = answer(hypotheses=[HYP], requests=[read_request("src/x.ts", 1, 10)])
        round1 = answer(hypotheses=[HYP], requests=[read_request("src/y.ts", 1, 10)])
        round2 = answer(hypotheses=[HYP], requests=[read_request("src/z.ts", 1, 10)])  # answered for final only
        model = UnitModel([first, round1, round2, answer(), answer()])
        result = run(model, context=inventory(files=[record("src/a.ts", modified(["a"], ["b"]))], context_files=[]))
        phases = [c["phase"] for c in model.calls]
        self.assertEqual(phases, ["discovery", "investigation", "investigation", "verification"])
        self.assertIn("round 2 of at most 2", model.calls[2]["instruction"])
        verification = model.calls[-1]
        self.assertIn("src/z.ts", json.dumps(verification["cache_context"]))
        self.assertEqual([f["round"] for f in result["units"]["units"][0]["followups"]], [1, 2])

    def test_no_second_round_without_new_answered_evidence(self):
        first = answer(hypotheses=[HYP], requests=[read_request("src/x.ts", 1, 10)])
        model = UnitModel([first, answer(hypotheses=[HYP]), answer()])
        run(model, context=inventory(files=[record("src/a.ts", modified(["a"], ["b"]))], context_files=[]))
        self.assertEqual([c["phase"] for c in model.calls], ["discovery", "investigation", "verification"])

    def test_every_primary_units_requests_are_read_before_any_paid_follow_up_and_rounds_are_round_robin(self):
        order = []

        class Ordered(UnitModel):
            def invoke(self, system, payload, budget, phase, **kwargs):
                order.append(("call", phase, kwargs["cache_context"].get("unit", {}).get("index")))
                return super().invoke(system, payload, budget, phase, **kwargs)

        def reader(repository, sha, path, start, end):
            order.append(("read", path))
            return numbered_file(50)(repository, sha, path, start, end)

        a = answer(hypotheses=[HYP], requests=[read_request("src/ra.ts", 1, 10)])
        b = answer(hypotheses=[dict(HYP, path="lib/b.py")], requests=[read_request("src/rb.ts", 1, 10)])
        r1a = answer(hypotheses=[HYP], requests=[read_request("src/ra2.ts", 1, 10)])
        r1b = answer(hypotheses=[HYP], requests=[read_request("src/rb2.ts", 1, 10)])
        model = Ordered([a, b, r1a, r1b, answer(), answer(), answer()])
        run(model, context=self.big_two_units(), read_file=reader)
        first_followup = next(i for i, o in enumerate(order) if o[:2] == ("call", "investigation"))
        reads_before = [o[1] for o in order[:first_followup] if o[0] == "read"]
        self.assertIn("src/ra.ts", reads_before)
        self.assertIn("src/rb.ts", reads_before)
        followups = [o[2] for o in order if o[:2] == ("call", "investigation")]
        self.assertEqual(followups, [1, 2, 1, 2])  # round-robin: both round 1s before any round 2


class FinalEvidenceTests(unittest.TestCase):  # M3, M6
    # Lines outside any candidate window (windows are +-40 lines around cited lines), so they arrive only as
    # a collector snippet.
    SNIPPET = {"path": "src/a.ts", "ref": "head", "content": "300: helper()\n301: return x",
               "start_line": 300, "end_line": 301, "reason": "changed_neighborhood", "line_numbered": True}

    def context(self, snippets):
        return inventory(files=[record("src/a.ts", modified(["a"], ["b"]))], context_files=snippets)

    def test_a_unit_snippet_reaches_the_final_verification(self):
        model = UnitModel([answer(hypotheses=[HYP]), answer()])
        result = run(model, context=self.context([self.SNIPPET]))
        verification = [c for c in model.calls if c["phase"] == "verification"][0]
        self.assertIn("300: helper()", json.dumps(verification["cache_context"]))
        self.assertEqual(result["units"]["reads"]["snippets_offered"], 1)

    def exclusion_run(self, snippet, cite):
        class Tight(UnitModel):
            def required_budget(self, system, payload, phase, **kwargs):
                if phase == "verification":
                    windows = json.dumps((kwargs.get("cache_context") or {}).get("windows", []))
                    return 30_000 + (400_000 if "helper()" in windows else 0)  # the snippet alone does not fit
                return super().required_budget(system, payload, phase, **kwargs)
        hypothesis = dict(HYP, path="src/a.ts" if cite else "src/other.ts",
                          trigger="t", mechanism="m", check="src/a.ts:R1" if cite else "no file cited")
        model = Tight([answer(hypotheses=[hypothesis]), answer()])
        return run(model, context=self.context([snippet]))

    def test_an_uncited_excluded_snippet_is_a_scope_note_and_a_cited_one_stays_a_gap(self):
        incidental = self.exclusion_run(self.SNIPPET, cite=False)
        self.assertTrue(any("not included in the final verification (budget); no candidate" in n
                            for n in incidental["scope_notes"]))
        self.assertFalse(any("collector snippet" in g for g in incidental["coverage_gaps"]))
        material = self.exclusion_run(self.SNIPPET, cite=True)
        self.assertTrue(any(g.startswith("collector snippet src/a.ts (head) lines 300-301 excluded") for g in
                            material["coverage_gaps"]))
        self.assertEqual(material["status"], "partial")

    def test_all_open_questions_are_carried_and_trimmed_by_name_before_candidates(self):
        many = answer(hypotheses=[HYP], gaps=[f"question {n}" for n in range(12)])
        model = UnitModel([many, answer()])
        run(model, context=self.context([]))
        verification = [c for c in model.calls if c["phase"] == "verification"][0]
        self.assertEqual(len(verification["payload"]["open_questions"]), 12)

        class Tight(UnitModel):
            def required_budget(self, system, payload, phase, **kwargs):
                if phase == "verification":
                    return 30_000 + 40_000 * len(payload.get("open_questions") or [])
                return super().required_budget(system, payload, phase, **kwargs)
        tight = Tight([many, answer()])
        result = run(tight, context=self.context([]))
        trimmed = [g for g in result["coverage_gaps"] if g.startswith("open question not passed to the final "
                                                                      "verification (final budget)")]
        self.assertTrue(trimmed)
        self.assertEqual(len(result["audit"]["items"]), 1)  # the candidate is kept
        self.assertIn("verification", [c["phase"] for c in tight.calls])


class DispositionIntegrationTests(unittest.TestCase):
    def small(self):
        return inventory(files=[record("src/a.ts", modified(["a"], ["b"]))], context_files=[])

    def test_the_final_instruction_and_preflight_carry_the_disposition_contract(self):
        from dispositions import VERIFICATION_DISPOSITIONS
        final = dict(answer(), dispositions=[{"candidate_id": "c1", "verdict": "rejected",
                    "reason": "The supplied guard covers this state", "evidence": ["src/a.ts:R1 guard"]}])
        hypothesis = dict(HYP, path="src/a.ts", check="src/a.ts:R1 supplied guard")
        model = UnitModel([answer(hypotheses=[hypothesis]), final])
        result = run(model, context=self.small())
        verification = [c for c in model.calls if c["phase"] == "verification"][0]
        self.assertIn(VERIFICATION_DISPOSITIONS, verification["instruction"])
        self.assertIn("question_resolutions", verification["instruction"])
        self.assertEqual([i["candidate_id"] for i in verification["payload"]["candidates_and_hypotheses"]], ["c1"])
        self.assertEqual(result["status"], "complete")  # the fixture accounted for the candidate
        self.assertEqual([r.get("status") for r in result["audit"]["dispositions"]], ["rejected"])

    def test_missing_unresolved_or_unknown_dispositions_keep_a_reviewed_case_partial(self):
        cases = {
            "missing": dict(answer(), dispositions=None),
            "unresolved": dict(answer(), dispositions=[{"candidate_id": "c1", "verdict": "unresolved",
                                                        "reason": "the producer was not supplied", "evidence": []}]),
            "unknown id": dict(answer(), dispositions=[{"candidate_id": "c9", "verdict": "rejected", "reason": "r",
                                                        "evidence": ["src/a.ts:R1 — guard"]}]),
        }
        for name, final in cases.items():
            with self.subTest(case=name):
                model = UnitModel([answer(hypotheses=[HYP]), final])
                result = run(model, context=self.small())
                self.assertEqual(result["status"], "partial")
                self.assertTrue(result["coverage_gaps"])

    def test_the_helper_receives_the_post_validation_findings(self):
        import units as module
        seen = {}
        original = module.audit_dispositions

        def spy(items, final, validated, **kwargs):
            seen["validated"], seen["supplied"] = validated, kwargs.get("supplied_paths")
            return original(items, final, validated, **kwargs)
        module.audit_dispositions = spy
        try:
            bad = {"path": "src/a.ts", "line": 999, "priority": "P1", "title": "t", "body": "b", "confidence": 0.95,
                   "evidence": ["src/a.ts:R999"], "counterargument": "c"}   # line 999 is not a changed anchor
            model = UnitModel([answer(hypotheses=[HYP]), answer(findings=[bad])])
            result = run(model, context=self.small())
        finally:
            module.audit_dispositions = original
        self.assertEqual(seen["validated"], result["findings"])
        verification = [c for c in model.calls if c["phase"] == "verification"][0]
        self.assertEqual(seen["supplied"], units.final_supplied_paths(verification["cache_context"]))
        self.assertTrue(seen["supplied"])  # the admitted final package's paths, passed explicitly
        self.assertEqual(result["findings"], [])  # the unchanged validator rejected the unanchored finding

    def test_an_invalid_line_raw_finding_cannot_clear_its_candidate(self):
        bad = {"path": "src/a.ts", "line": 999, "priority": "P1", "title": "t", "body": "b", "confidence": 0.95,
               "evidence": ["src/a.ts:R999"], "counterargument": "c"}
        final = dict(answer(findings=[bad]), dispositions=[{"candidate_id": "c1", "verdict": "supported",
                                                            "reason": "confirmed", "evidence": ["src/a.ts:R1 — x"],
                                                            "finding_index": 0}])
        model = UnitModel([answer(hypotheses=[dict(HYP, path="src/a.ts")]), final])
        result = run(model, context=self.small())
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["status"], "partial")
        self.assertNotEqual([r.get("status") for r in result["audit"]["dispositions"]], ["supported"])

    def test_supplied_paths_come_from_the_admitted_final_package_only(self):
        package = {"hunks": [{"path": "src/a.ts"}], "windows": [{"path": "src/b.ts", "ref": "h"}, {"x": 1}],
                   "changed_files": [{"path": "src/unshown.ts"}]}
        self.assertEqual(units.final_supplied_paths(package), {"src/a.ts", "src/b.ts"})

    def test_candidate_ids_are_controller_owned_and_survive_trimming(self):
        class Tight(UnitModel):
            def required_budget(self, system, payload, phase, **kwargs):
                if phase == "verification":
                    return 30_000 + max(0, len(payload.get("candidates_and_hypotheses", [])) - 2) * 400_000
                return super().required_budget(system, payload, phase, **kwargs)
        hypotheses = [dict(HYP, trigger=f"state {n}", candidate_id="model-chosen") for n in range(4)]
        model = Tight([answer(hypotheses=hypotheses), answer()])
        result = run(model, context=self.small())
        self.assertEqual([i["candidate_id"] for i in result["audit"]["items"]], ["c1", "c2"])


def _followup_instruction_probe():
    """The instruction a real follow-up call receives (round 1), captured through a minimal review."""
    model = UnitModel([answer(hypotheses=[HYP], requests=[{"kind": "read", "path": "src/a.ts", "ref": "head",
                                                           "start": 1, "end": 5}]), answer(), answer()])
    run(model, context=inventory(files=[record("src/a.ts", modified(["a"], ["b"]))], context_files=[]))
    return next(c["instruction"] for c in model.calls if c["phase"] == "investigation")


class DocumentationSelectionIntegrationTests(unittest.TestCase):
    """Generic selection (agent25's revision 2), wired per its integration contract."""
    def react_change(self):
        patch = modified(["import React from 'react';", "const a = 1;"],
                         ["import React, { useEffect } from 'react';", "useEffect(() => { subscribe(); }, []);"])
        return inventory(files=[record("src/view.tsx", patch)], context_files=[])

    def test_an_eligible_change_gets_the_document_in_the_final_package_with_its_basis(self):
        model = UnitModel([answer(hypotheses=[dict(HYP, path="src/view.tsx")]), answer()])
        result = run(model, context=self.react_change())
        self.assertEqual(result["units"]["documentation_auto"]["selected"], ["react.strict-mode"])
        self.assertEqual(result["units"]["documentation_auto"]["by_basis"], {"api": 1, "runtime": 0})
        verification = [c for c in model.calls if c["phase"] == "verification"][0]
        self.assertIn("react.strict-mode", json.dumps(verification["cache_context"]))
        self.assertIn("selection.basis runtime", verification["instruction"])
        self.assertIn("selection.basis runtime", model.calls[0]["instruction"])
        self.assertIn(units.DOCUMENTATION_RUNTIME_BASIS, _followup_instruction_probe())  # follow-ups carry it too
        statuses = [e.get("status") for e in result["documentation"]["requests"]]
        self.assertIn("auto_selected", statuses)

    def test_a_request_refused_because_automatic_picks_filled_the_slots_is_named(self):
        import docs_evidence
        registry = docs_evidence.DocRegistry()
        others = [i for i in sorted(registry.records) if i != "react.strict-mode"][:2]
        requests = [{"kind": "doc", "doc": d} for d in others]
        model = UnitModel([answer(hypotheses=[dict(HYP, path="src/view.tsx")], requests=requests)] + [answer()] * 4)
        result = run(model, context=self.react_change())
        self.assertTrue(any(e.get("status") == "over_limit" and e.get("cause") == "auto_selected"
                            for e in result["documentation"]["requests"]))
        self.assertTrue(any("filled" in g and "automatic selection" in g for g in result["coverage_gaps"]))

    def test_an_automatic_document_excluded_at_the_final_fit_is_a_named_gap(self):
        class Tight(UnitModel):
            def required_budget(self, system, payload, phase, **kwargs):
                if phase == "verification":
                    windows = json.dumps((kwargs.get("cache_context") or {}).get("windows", []))
                    return 30_000 + (400_000 if "react.strict-mode" in windows else 0)
                return super().required_budget(system, payload, phase, **kwargs)
        model = Tight([answer(hypotheses=[dict(HYP, path="src/view.tsx")]), answer()])
        result = run(model, context=self.react_change())
        self.assertTrue(any("react.strict-mode" in g or "Requested evidence" in g for g in result["coverage_gaps"]))
        self.assertEqual(result["status"], "partial")

    def test_publisher_diagnostics_count_automatic_picks_by_basis(self):
        import publish
        model = UnitModel([answer(hypotheses=[dict(HYP, path="src/view.tsx")]), answer()])
        result = run(model, context=self.react_change())
        text = json.dumps(publish.review_diagnostics(result))
        self.assertIn('"auto_selected": 1', text)
        self.assertIn('"auto_selected_api": 1', text)

    def test_no_eligible_change_selects_nothing(self):
        model = UnitModel([answer(hypotheses=[HYP]), answer()])
        result = run(model, context=inventory(files=[record("src/a.ts", modified(["a"], ["b"]))], context_files=[]))
        self.assertEqual(result["units"]["documentation_auto"]["selected"], [])


class ReadDiagnosticsTests(unittest.TestCase):
    def test_guarded_read_elapsed_and_overruns_are_recorded(self):
        model = UnitModel([answer(hypotheses=[HYP], requests=[read_request("src/x.ts", 1, 10)])] + [answer()] * 4)
        result = run(model, context=inventory(files=[record("src/a.ts", modified(["a"], ["b"]))], context_files=[]))
        reads = result["units"]["reads"]
        for key in ("reads", "cached", "seconds", "max_read_seconds", "allowance_overruns", "not_started_time", "pages"):
            self.assertIn(key, reads)
        self.assertEqual(reads["allowance_overruns"], 0)
        self.assertGreaterEqual(reads["reads"], 1)


class FinalHoldTests(unittest.TestCase):  # C1
    def test_the_hold_includes_known_requested_evidence_so_optional_work_cannot_starve_the_final_proof(self):
        from test_units import sample_inventory

        class Costly(UnitModel):
            def required_budget(self, system, payload, phase, **kwargs):
                if phase == "verification" and payload.get("candidates_and_hypotheses"):
                    proof = "src/proof.ts" in json.dumps(kwargs.get("cache_context") or {})
                    return 30_000 + (330_000 if proof else 0)  # the requested proof is what makes it expensive
                return super().required_budget(system, payload, phase, **kwargs)
        first = answer(hypotheses=[HYP], requests=[read_request("src/proof.ts", 1, 20)])
        model = Costly([first] + [answer()] * 12)
        result = run(model, context=sample_inventory())
        log = result["units"]["units"]
        self.assertFalse(any(u["role"] == "deferred" and u["audited"] for u in log))
        self.assertTrue(any(g.startswith("deferred unit") and "(budget)" in g for g in result["coverage_gaps"]))
        verification = [c for c in model.calls if c["phase"] == "verification"]
        self.assertEqual(len(verification), 1)
        self.assertIn("src/proof.ts", json.dumps(verification[0]["cache_context"]))  # the proof reached the verifier


class SchemaCoherenceTests(unittest.TestCase):
    def test_trusted_schema_and_the_disposition_suffix_agree(self):
        import re
        import prompts
        from dispositions import VERIFICATION_DISPOSITIONS
        schema = prompts.POLICY.split("Return JSON only, with exactly these fields:", 1)[1]
        self.assertIn('"dispositions":[{"candidate_id":"c1","verdict":"supported|rejected|unresolved",', schema)
        keys = lambda text: re.findall(r'"(candidate_id|verdict|reason|evidence|finding_index)"', text)
        self.assertEqual(sorted(set(keys(schema.split('"dispositions":', 1)[1].split("}]", 1)[0]))),
                         sorted(set(keys(VERIFICATION_DISPOSITIONS))))
        self.assertIn("supported|rejected|unresolved", VERIFICATION_DISPOSITIONS)
        self.assertIn("Return dispositions only in verification and only when the candidates carry controller-supplied",
                      prompts.POLICY)
        self.assertEqual(units.VERSION, prompts.VERSION)

    def test_unit_questions_stay_named_gaps_while_carried(self):
        model = UnitModel([answer(hypotheses=[HYP], gaps=["is the producer's shape known?"]), answer()])
        result = run(model, context=inventory(files=[record("src/a.ts", modified(["a"], ["b"]))], context_files=[]))
        self.assertIn("Question q1: no unique explicit resolution: unit 1: is the producer's shape known?",
                      result["coverage_gaps"])
        verification = [c for c in model.calls if c["phase"] == "verification"][0]
        self.assertEqual(verification["payload"]["open_questions"], [{"question_id": "q1",
                         "question": "unit 1: is the producer's shape known?"}])
        self.assertEqual(result["status"], "partial")


class ReadDeadlineTests(unittest.TestCase):  # C2
    class Clocked(UnitModel):
        def __init__(self, answers, deadline, now, step=0.0):
            super().__init__(answers)
            self.deadline, self.now, self.step = deadline, now, step

        def clock(self):
            return self.now

        def invoke(self, system, payload, budget, phase, **kwargs):
            import provider
            if self.deadline - self.now < 105:
                raise provider.DeadlineReached("Review deadline reached before a model request; no request sent")
            self.now += self.step
            return super().invoke(system, payload, budget, phase, **kwargs)

    def test_unit_reads_need_the_hold_plus_a_read_timeout_and_are_named_otherwise(self):
        log = []
        # 250 s left: the unit call may start (250 - 135 >= 105). It takes 60 s, leaving 190 s: a read needs the
        # 135 s hold plus a 60 s read timeout (195 s), so it may not start; it is named, never silently skipped.
        model = self.Clocked([answer(hypotheses=[HYP], requests=[read_request("src/x.ts", 1, 10)])] + [answer()] * 4,
                             deadline=420.0, now=170.0, step=60.0)
        result = run(model, context=inventory(files=[record("src/a.ts", modified(["a"], ["b"]))], context_files=[]),
                     read_file=numbered_file(50, log=log))
        self.assertTrue(any("read of src/x.ts" in g and "(time)" in g for g in result["coverage_gaps"]))
        self.assertEqual([p for p, _, _ in log if p == "src/x.ts"], [])
        self.assertEqual(model.deadline, 420.0)

    def test_final_gathering_reads_are_guarded_by_their_own_threshold(self):
        self.assertEqual(units.FINAL_TIME_HOLD_SECONDS + units.READ_TIMEOUT_SECONDS, 195)
        self.assertEqual(units.VERIFICATION_COUNT_SECONDS + units.VERIFICATION_MIN_USEFUL_SECONDS
                         + units.READ_TIMEOUT_SECONDS, 165)


if __name__ == "__main__":
    unittest.main()
