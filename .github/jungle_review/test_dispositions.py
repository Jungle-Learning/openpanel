"""Coverage must not become clean when verification silently loses a candidate."""
import copy
import json
import unittest

from dispositions import MAX_RECORDS, VERIFICATION_DISPOSITIONS, audit_dispositions, identify_candidates
from review import validated_findings


CONTEXT = {"head_sha": "a" * 40, "base_sha": "b" * 40, "files": [
    {"path": "src/a.py", "patch": "@@ -1,2 +1,2 @@\n-old\n+new\n tail\n", "changed_lines": [1]}]}


def claim(**changes):
    return {"path": "src/a.py", "line": 1, "priority": "P2", "title": "Preserve the rejected result",
            "body": "The failure branch returns success and discards the failure.", "confidence": .95,
            "evidence": ["src/a.py:R1 returns success for a failed result"],
            "counterargument": "The caller relies on this return value.", **changes}


def decision(key="c1", verdict="rejected", **changes):
    return {"candidate_id": key, "verdict": verdict,
            "reason": "The caller handles the explicit failure before using this value.",
            "evidence": ["src/a.py:R2 checks the failure"], **changes}


def inspect(items=None, dispositions=None, findings=None):
    items = identify_candidates([{"path": "src/a.py", "kind": "hypothesis"}]) if items is None else items
    result = {"findings": findings or [], "dispositions": dispositions}
    valid, finding_gaps = validated_findings(result, CONTEXT)
    audit = audit_dispositions(items, result, valid, supplied_paths={"src/a.py"})
    return audit, finding_gaps, valid


class DispositionTests(unittest.TestCase):
    def test_ids_are_controller_owned_deterministic_and_do_not_mutate_input(self):
        source = [{"path": "a", "candidate_id": "injected"}, {"path": "b"}]
        before = copy.deepcopy(source)
        result = identify_candidates(source)
        self.assertEqual([x["candidate_id"] for x in result], ["c1", "c2"])
        self.assertEqual(source, before)
        self.assertEqual(result, identify_candidates(source))
        self.assertEqual(result[:1][0]["candidate_id"], "c1")  # trimming does not renumber retained entries

    def test_supported_links_to_real_validator_output(self):
        audit, gaps, valid = inspect(dispositions=[decision(verdict="supported", finding_index=0)],
                                     findings=[claim()])
        self.assertEqual(gaps, [])
        self.assertEqual(len(valid), 1)
        self.assertEqual(audit["coverage_gaps"], [])
        self.assertEqual(audit["records"][0]["status"], "supported")

    def test_supported_cannot_clear_confidence_or_anchor_failure(self):
        for finding in [claim(confidence=.89), claim(line=400), claim(evidence=[])]:
            with self.subTest(finding=finding):
                audit, gaps, valid = inspect(dispositions=[decision(verdict="supported", finding_index=0)],
                                             findings=[finding])
                self.assertTrue(gaps)
                self.assertEqual(valid, [])
                self.assertTrue(audit["coverage_gaps"])
                self.assertEqual(audit["records"][0]["status"], "invalid")

    def test_matching_only_path_and_line_cannot_clear_a_different_claim(self):
        items = identify_candidates([{"path": "src/a.py"}])
        raw = {"findings": [claim()], "dispositions": [decision(verdict="supported", finding_index=0)]}
        audit = audit_dispositions(items, raw, [claim(body="A different mechanism at the same line")],
                                   supplied_paths={"src/a.py"})
        self.assertTrue(audit["coverage_gaps"])

    def test_supported_cannot_clear_a_candidate_on_another_path(self):
        items = identify_candidates([{"path": "src/b.py"}])
        audit, _, valid = inspect(items, [decision(verdict="supported", finding_index=0)], [claim()])
        self.assertEqual(len(valid), 1)
        self.assertTrue(any("not on the candidate's path" in note for note in audit["coverage_gaps"]))

    def test_rejected_requires_locator_on_a_supplied_path(self):
        for text in ["looks ok", "unseen.py:R2 handles it", "src/a.py:R0 is impossible",
                     "src/a.py:2 has no side", "src/a.py:R-1 is invalid", "/src/a.py:R2 is a different path",
                     "src/a.py:R2x is not a line", "ßsrc/a.py:R2 is a different path"]:
            with self.subTest(text=text):
                audit, _, _ = inspect(dispositions=[decision(evidence=[text])])
                self.assertTrue(audit["coverage_gaps"])
        audit, _, _ = inspect(dispositions=[decision(evidence=["`src/a.py:L2` handled this already"])])
        self.assertEqual(audit["coverage_gaps"], [])

    def test_path_patterns_are_escaped_and_empty_supplied_set_cannot_clear_rejection(self):
        items = identify_candidates([{"path": "a+b.py"}])
        result = {"findings": [], "dispositions": [decision(evidence=["a+b.py:R2 checks it"])]}
        self.assertEqual(audit_dispositions(items, result, [], supplied_paths={"a+b.py"})["coverage_gaps"], [])
        result["dispositions"][0]["evidence"] = ["aaab.py:R2 is not the supplied path"]
        self.assertTrue(audit_dispositions(items, result, [], supplied_paths={"a+b.py"})["coverage_gaps"])
        self.assertTrue(audit_dispositions(items, result, [], supplied_paths=set())["coverage_gaps"])

    def test_sorting_and_deduplication_do_not_break_raw_finding_index(self):
        findings = [claim(), claim(priority="P1", title="Guard the other failure"), claim()]
        audit, gaps, valid = inspect(dispositions=[decision(verdict="supported", finding_index=2)],
                                     findings=findings)
        self.assertEqual(gaps, [])
        self.assertEqual(len(valid), 2)
        self.assertEqual(valid[0]["priority"], "P1")
        self.assertEqual(audit["coverage_gaps"], [])

    def test_one_validated_finding_can_resolve_two_duplicate_candidates(self):
        items = identify_candidates([{"path": "src/a.py"}, {"path": "src/a.py"}])
        audit, _, _ = inspect(items, [decision("c1", "supported", finding_index=0),
                                     decision("c2", "supported", finding_index=0)], [claim()])
        self.assertEqual(audit["coverage_gaps"], [])
        self.assertEqual(len(audit["records"]), 2)

    def test_rejected_requires_reason_and_code_evidence(self):
        audit, _, _ = inspect(dispositions=[decision()])
        self.assertEqual(audit["coverage_gaps"], [])
        for changes in [{"reason": " "}, {"reason": "x" * 801}, {"evidence": []},
                        {"evidence": [1]}, {"evidence": ["x" * 701]}, {"evidence": ["x"] * 9}]:
            with self.subTest(changes=changes):
                audit, _, _ = inspect(dispositions=[decision(**changes)])
                self.assertTrue(audit["coverage_gaps"])

    def test_unresolved_always_keeps_gap_and_missing_evidence_is_not_rejection(self):
        audit, _, _ = inspect(dispositions=[decision(verdict="unresolved", evidence=[],
                                                      reason="The producer has not been supplied.")])
        self.assertTrue(audit["coverage_gaps"])
        self.assertEqual(audit["records"][0]["status"], "unresolved")

    def test_missing_array_empty_array_and_malformed_rows_leave_named_candidate(self):
        for value in [None, {}, [], [None], [{"candidate_id": []}], [{"candidate_id": 1}]]:
            with self.subTest(value=value):
                audit, _, _ = inspect(dispositions=value)
                self.assertTrue(any("c1: dropped without a verdict" in note for note in audit["coverage_gaps"]))
                self.assertEqual(audit["records"][0]["status"], "missing")

    def test_duplicate_even_identical_dispositions_fail_closed(self):
        audit, _, _ = inspect(dispositions=[decision(), decision()])
        self.assertTrue(audit["coverage_gaps"])
        self.assertEqual(audit["records"][0]["status"], "invalid")

    def test_malformed_extra_record_cannot_be_ignored_after_all_candidates_resolved(self):
        audit, _, _ = inspect(dispositions=[decision(), None])
        self.assertEqual(audit["records"][0]["status"], "rejected")
        self.assertTrue(any("malformed" in note for note in audit["coverage_gaps"]))

    def test_unknown_candidate_is_named_without_echoing_untrusted_id(self):
        audit, _, _ = inspect(dispositions=[decision(), decision("@external-mention")])
        self.assertTrue(any("unknown candidate" in note for note in audit["coverage_gaps"]))
        self.assertNotIn("@external-mention", json.dumps(audit))

    def test_invalid_support_indices_including_bool_do_not_clear_candidate(self):
        for index in [True, -1, 2, "0", None]:
            with self.subTest(index=index):
                audit, _, _ = inspect(dispositions=[decision(verdict="supported", finding_index=index)],
                                     findings=[claim()])
                self.assertTrue(audit["coverage_gaps"])

    def test_non_supported_cannot_link_a_finding(self):
        for verdict in ["rejected", "unresolved"]:
            audit, _, _ = inspect(dispositions=[decision(verdict=verdict, finding_index=0)], findings=[claim()])
            self.assertTrue(audit["coverage_gaps"])
            self.assertEqual(audit["records"][0]["status"], "invalid")

    def test_unknown_verdict_is_not_a_safe_rejection(self):
        audit, _, _ = inspect(dispositions=[decision(verdict="safe")])
        self.assertTrue(audit["coverage_gaps"])
        self.assertEqual(audit["records"][0]["status"], "invalid")

    def test_overflow_cannot_hide_missing_candidate(self):
        items = identify_candidates([{"path": "a"}, {"path": "b"}])
        rows = [decision()] * MAX_RECORDS + [decision("c2")]
        audit, _, _ = inspect(items, rows)
        self.assertTrue(any("limit exceeded" in note for note in audit["coverage_gaps"]))
        self.assertTrue(any("c2: dropped" in note for note in audit["coverage_gaps"]))

    def test_empty_candidate_list_needs_no_decision_but_extra_decisions_are_not_ignored(self):
        audit, _, _ = inspect([], [])
        self.assertEqual(audit, {"records": [], "coverage_gaps": []})
        audit, _, _ = inspect([], [decision()])
        self.assertTrue(audit["coverage_gaps"])

    def test_duplicate_controller_ids_are_programming_error(self):
        with self.assertRaises(ValueError):
            audit_dispositions([{"candidate_id": "c1"}, {"candidate_id": "c1"}], {}, [], supplied_paths=set())

    def test_supplied_paths_are_explicit_not_inferred_from_candidates(self):
        with self.assertRaises(TypeError):
            audit_dispositions(identify_candidates([{"path": "a"}]), {}, [])
        with self.assertRaises(ValueError):
            audit_dispositions([], {}, [], supplied_paths="a")

    def test_prompt_schema_example_is_valid_json(self):
        start = VERIFICATION_DISPOSITIONS.index('{"candidate_id"')
        end = VERIFICATION_DISPOSITIONS.index('}.', start) + 1
        parsed = json.loads(VERIFICATION_DISPOSITIONS[start:end])
        self.assertEqual(parsed["candidate_id"], "c1")
        self.assertIn("Missing context is not", VERIFICATION_DISPOSITIONS)


if __name__ == "__main__":
    unittest.main()
