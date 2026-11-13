import copy
import unittest

from provider import Budget, BudgetExceeded, ReviewUnavailable
from review import compact_context, merge_proposals, numbered_patch, retrieve, review_context, validated_findings

CONTEXT = {"repository": "Jungle-Learning/example", "number": 1,
           "head_sha": "a" * 40, "base_sha": "b" * 40,
           "files": [{"path": "app.py", "changed_lines": [2, 3]}],
           "tree": ["app.py", "helpers.py"], "coverage": {"complete": True, "notes": []}}
FINDING = {"path": "app.py", "line": 2, "priority": "P1", "title": "Preserve the authorization check",
           "body": "An unowned ID now reaches storage before ownership is checked, exposing other accounts.",
           "confidence": 0.97, "evidence": ["app.py:2 — storage call precedes guard"],
           "counterargument": "The caller passes user-controlled IDs without checking ownership."}


class FakeModel:
    model = "claude-opus-5-5"
    prices = {'input': 4, 'output': 20}
    def __init__(self, results):
        self.results, self.payloads = iter(results), []
    def invoke(self, system, payload, budget, phase, **kwargs):
        self.payloads.append(payload)
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result
    def required_budget(self, *args, **kwargs):
        return 160000


def result(findings=None, requests=None, gaps=None):
    return {"findings": findings or [], "requests": requests or [], "coverage_gaps": gaps or [], "summary": "Reviewed supplied code."}


class ReviewTests(unittest.TestCase):
    def test_exact_frozen_sha_requests_work_but_other_revisions_cannot_be_read(self):
        calls = []
        requests = [{"kind": "read", "path": "app.py", "ref": ref, "start": 1, "end": 5}
                    for ref in [CONTEXT["head_sha"], CONTEXT["base_sha"], "c" * 40, "main"]]
        def read(*args):
            calls.append(args)
            return {"content": "evidence"}
        evidence, notes = retrieve(result(requests=requests), CONTEXT, read)
        self.assertEqual([call[1] for call in calls], [CONTEXT["head_sha"], CONTEXT["base_sha"]])
        self.assertEqual(len(evidence), 2)
        self.assertEqual(notes, ["Rejected invalid context revision"] * 2)

    def test_overlapping_read_and_search_keep_one_copy_with_actual_line_numbers(self):
        extra = [{"result": {"path": "a.py", "ref": "a" * 40, "start_line": 5,
                              "content": "five\nsix", "truncated": True}},
                 {"result": {"path": "a.py", "ref": "a" * 40, "windows": [
                     {"start_line": 6, "content": "6: six\n7: seven"}]}},
                 {"result": {"path": "b.py", "ref": "a" * 40, "error": "not_found"}}]
        result = compact_context(extra)
        self.assertEqual(result[0]['content'], '5: five\n6: six\n7: seven')
        self.assertTrue(result[0]['truncated'])
        self.assertEqual(result[1], extra[2])
        self.assertIn('content', extra[0]['result'])

    def test_deletion_only_regression_can_anchor_to_adjacent_context(self):
        patch = '@@ -1,3 +1,2 @@\n def remove():\n-    authorize()\n     delete()'
        annotated, anchors = numbered_patch(patch)
        self.assertEqual(anchors, {1, 2})
        self.assertIn('L2 -    authorize()', annotated)
        self.assertIn('R2      delete()', annotated)
        context = copy.deepcopy(CONTEXT)
        context['files'][0].update(patch=patch, changed_lines=[])
        findings, errors = validated_findings(result([FINDING]), context)
        self.assertEqual(len(findings), 1)
        self.assertFalse(errors)

    def test_imprecise_provisional_anchor_reaches_verifier_for_repair(self):
        proposal = {**FINDING, 'line': 999, 'confidence': 0.7}
        model = FakeModel([result([proposal]), result([FINDING])])
        outcome = review_context(CONTEXT, model, lambda *args: '')
        self.assertEqual(model.payloads[1]['proposed_findings'], [proposal])
        self.assertEqual(len(outcome['findings']), 1)

    def test_investigation_omission_does_not_erase_a_supported_proposal(self):
        request = {"kind": "read", "path": "helpers.py", "ref": "head", "start": 1, "end": 10}
        model = FakeModel([result([FINDING], requests=[request]), result(), result([FINDING])])
        outcome = review_context(CONTEXT, model, lambda *args: {"content": "guard"})
        self.assertEqual(model.payloads[-1]['proposed_findings'], [FINDING])
        self.assertEqual(len(outcome['findings']), 1)

    def test_retrieval_transport_failure_cannot_be_clean(self):
        request = {"kind": "read", "path": "helpers.py", "ref": "head", "start": 1, "end": 10}
        model = FakeModel([result(requests=[request]), result(), result()])
        outcome = review_context(CONTEXT, model, lambda *args: {"error": "read failed (RuntimeError)"})
        self.assertEqual(outcome['status'], 'unavailable')
        self.assertTrue(outcome['retryable'])
        self.assertEqual(len(model.payloads), 1)  # no further paid calls after failed retrieval

    def test_discovery_protects_final_call_before_any_generation(self):
        class CostedModel(FakeModel):
            def required_budget(self, *args, **kwargs):
                return 300000
            def invoke(self, system, payload, budget, phase, **kwargs):
                amount = kwargs['ceiling'] if phase == 'discovery' else 300000
                if phase == 'discovery':
                    self.discovery_allowance = amount
                budget.reserve(amount, {'phase': phase})
                return result()
        model = CostedModel([])
        outcome = review_context(CONTEXT, model, lambda *args: {})
        self.assertEqual(model.discovery_allowance, 66000)
        self.assertEqual(outcome['status'], 'complete')
        self.assertEqual(outcome['budget']['cost_upper_bound_usd'], 0.366)

    def test_unaffordable_final_pass_spends_nothing_on_discovery(self):
        model = FakeModel([])
        model.required_budget = lambda *a, **k: 500000
        outcome = review_context(CONTEXT, model, lambda *args: {})
        self.assertEqual(outcome['status'], 'unavailable')
        self.assertEqual(outcome['budget']['cost_upper_bound_usd'], 0)
        self.assertFalse(model.payloads)

    def test_failed_evidence_preflight_remains_retryable_and_withholds_findings(self):
        request = {'kind': 'read', 'path': 'helpers.py', 'ref': 'head', 'start': 1, 'end': 10}
        model = FakeModel([result(requests=[request]), result(), result([FINDING])])
        def required(*args, **kwargs):
            if args[1].get('additional_context'):
                raise ReviewUnavailable('Provider HTTP 503')
            return 100000
        model.required_budget = required
        outcome = review_context(CONTEXT, model, lambda *args: {'content': 'guard'})
        self.assertEqual(outcome['status'], 'unavailable')
        self.assertTrue(outcome['retryable'])
        self.assertEqual(outcome['findings'], [])
        self.assertEqual(len(model.payloads), 1)  # preserve budget, never create then discard verified findings

    def test_evidence_over_pricing_tier_is_partial_not_a_transient_retry(self):
        request = {'kind': 'read', 'path': 'helpers.py', 'ref': 'head', 'start': 1, 'end': 10}
        model = FakeModel([result(requests=[request]), result(), result([FINDING])])
        def required(*args, **kwargs):
            if args[1].get('additional_context'):
                raise BudgetExceeded('Input exceeds verified standard pricing tier')
            return 100000
        model.required_budget = required
        outcome = review_context(CONTEXT, model, lambda *args: {'content': 'oversized evidence'})
        self.assertEqual(outcome['status'], 'partial')
        self.assertFalse(outcome['retryable'])
        self.assertEqual(len(outcome['findings']), 1)
        self.assertIn('Requested evidence could not fit the verified pricing tier', outcome['coverage_gaps'])

    def test_final_unresolved_hypothesis_cannot_be_clean(self):
        model = FakeModel([result(), {**result(), 'hypotheses': [{'trigger': 'still unknown'}]}])
        outcome = review_context(CONTEXT, model, lambda *args: {})
        self.assertEqual(outcome['status'], 'partial')

    def test_newest_proposals_survive_a_full_pool(self):
        old = [{**FINDING, 'title': str(i)} for i in range(12)]
        merged = merge_proposals(old, [{**FINDING, 'title': 'new'}])
        self.assertEqual(len(merged), 12)
        self.assertEqual(merged[-1]['title'], 'new')

    def test_discovery_evidence_is_admitted_only_if_verification_still_fits(self):
        request = {'path': 'helpers.py', 'ref': 'head', 'start': 1, 'end': 10}
        model = FakeModel([result(requests=[request]), result(), result()])
        model.required_budget = lambda *a, **k: 600000 if a[1].get('additional_context') else 100000
        outcome = review_context(CONTEXT, model, lambda *args: {'content': 'too big'})
        self.assertEqual(outcome['audit']['additional_context'], [])
        self.assertEqual(outcome['status'], 'partial')

    def test_fake_hunk_header_in_source_does_not_change_coordinates(self):
        patch = '@@ -1,1 +1,2 @@\n+@@ -9 +500 @@\n keep'
        annotated, anchors = numbered_patch(patch)
        self.assertEqual(anchors, {1, 2})
        self.assertIn('R1 +@@ -9 +500 @@', annotated)

    def test_invalid_lines_and_unsupported_claims_are_rejected(self):
        for field, value in [("line", 1), ("line", True), ("path", "../key"),
                             ("confidence", float("nan")), ("confidence", 0.8),
                             ("counterargument", ""), ("evidence", []), ("priority", "P3")]:
            with self.subTest(field=field, value=value):
                findings, errors = validated_findings(result([{**FINDING, field: value}]), CONTEXT)
                self.assertEqual(findings, [])
                self.assertTrue(errors)

    def test_verifier_can_reject_discovery_without_publishing(self):
        model = FakeModel([result([FINDING]), result()])
        outcome = review_context(CONTEXT, model, lambda *args: "")
        self.assertEqual(outcome["status"], "complete")
        self.assertEqual(outcome["findings"], [])
        self.assertEqual(outcome["discovery_findings"], 1)

    def test_verifier_failure_never_publishes_provisional_findings(self):
        model = FakeModel([result([FINDING]), ReviewUnavailable("timeout")])
        outcome = review_context(CONTEXT, model, lambda *args: "")
        self.assertEqual(outcome["status"], "unavailable")
        self.assertEqual(outcome["findings"], [])

    def test_malformed_provisional_identity_is_unavailable_not_a_crash(self):
        model = FakeModel([result([{**FINDING, 'path': []}])])
        outcome = review_context(CONTEXT, model, lambda *args: {})
        self.assertEqual(outcome['status'], 'unavailable')
        self.assertIn('budget', outcome)

    def test_context_requests_are_pinned_and_bounded(self):
        reads = []
        requests = [{"path": "helpers.py", "ref": "base", "start": 1, "end": 20},
                    {"path": "../../.env", "ref": "head", "start": 1, "end": 20}]
        model = FakeModel([result(requests=requests), result([FINDING]), result([FINDING])])
        def read(*args):
            reads.append(args)
            return "1: helper"
        outcome = review_context(CONTEXT, model, read)
        self.assertEqual(reads, [(CONTEXT["repository"], "b" * 40, "helpers.py", 1, 20)])
        self.assertEqual(outcome["status"], "complete")
        self.assertIn("Rejected an out-of-scope context request", outcome["retrieval_notes"])

    def test_truncated_diff_cannot_be_clean(self):
        context = copy.deepcopy(CONTEXT)
        context["coverage"].update(complete=False, omitted_files=["large.py"])
        outcome = review_context(context, FakeModel([result(), result()]), lambda *args: "")
        self.assertEqual(outcome["status"], "partial")
        self.assertTrue(outcome["coverage_gaps"])

    def test_invalid_final_result_remains_partial(self):
        outcome = review_context(CONTEXT, FakeModel([result(), result([{**FINDING, "line": 999}])]), lambda *args: "")
        self.assertEqual(outcome["status"], "partial")
        self.assertEqual(outcome["findings"], [])

    def test_duplicate_findings_get_one_comment(self):
        findings, _ = validated_findings(result([FINDING, FINDING]), CONTEXT)
        self.assertEqual(len(findings), 1)

    def test_reworded_existing_root_cause_reuses_only_known_same_path_id(self):
        context = copy.deepcopy(CONTEXT)
        prior = 'a' * 24
        context['existing_findings'] = {'findings': [{'fingerprint': prior, 'path': 'app.py', 'line': 2,
                                                      'current_line': FINDING['line'],
                                                      'priority': FINDING['priority']}]}
        finding = {**FINDING, 'title': 'A different wording for the same defect', 'existing_fingerprint': prior}
        valid, _ = validated_findings(result([finding]), context)
        self.assertEqual(valid[0]['fingerprint'], prior)
        context['existing_findings']['findings'][0]['path'] = 'elsewhere.py'
        valid, _ = validated_findings(result([finding]), context)
        self.assertNotEqual(valid[0]['fingerprint'], prior)

    def test_identical_titles_on_different_lines_are_not_silently_collapsed(self):
        valid, _ = validated_findings(result([FINDING, {**FINDING, 'line': 3}]), CONTEXT)
        self.assertEqual(len(valid), 2)


class LineNumberingTests(unittest.TestCase):
    def test_numbered_patch_keeps_git_coordinates_across_unicode_separators(self):
        patch = "@@ -1,2 +1,3 @@\n a\fb\n+c\u2028d\n e\x85f\n"
        text, anchors = numbered_patch(patch)
        self.assertEqual(anchors, {1, 2, 3})
        self.assertIn("R2 +c\u2028d", text)
        self.assertIn("R3  e\x85f", text)
        self.assertFalse(text.endswith("\n"))  # no phantom row from the final newline

    def test_compact_context_keeps_lines_with_separators(self):
        read = {"request": {"kind": "read"}, "result": {"path": "a.py", "ref": "h", "start_line": 10,
                                                        "content": "x\fy\nz\u2028w\nlast"}}
        search = {"request": {"kind": "search"}, "result": {"path": "a.py", "ref": "h", "windows": [
            {"start_line": 12, "content": "12: last\n13: tail\u2028more\n"}]}}
        compacted = compact_context([read, search])
        self.assertEqual(compacted[0]["content"],
                         "10: x\fy\n11: z\u2028w\n12: last\n13: tail\u2028more")


if __name__ == "__main__":
    unittest.main()
