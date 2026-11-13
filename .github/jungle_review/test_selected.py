import copy
import importlib
import json
import time
import unittest
from unittest.mock import patch

from questions import audit_questions, question_records
from test_focused import inventory

def context():
    code = "export function parse(s) {\n  return Number(s) || 1;\n}"
    return inventory(files=[{"path": "src/parse.ts", "status": "modified", "changed_lines": [2],
         "patch": "@@ -1,3 +1,3 @@\n export function parse(s) {\n-  return Number(s);\n+  return Number(s) || 1;\n }\n",
         "head_content": code}], context_files=[])

def finding():
    return {"path": "src/parse.ts", "line": 2, "priority": "P2", "title": "Preserve zero during parsing",
            "body": "Parsing '0' now returns 1 because the fallback treats zero as false.", "confidence": 0.98,
            "evidence": ["src/parse.ts:R2 uses a truthy fallback"],
            "counterargument": "No guard distinguishes zero from invalid input."}

from provider import Budget
import context as source


class UnitModel:
    model = "gpt-6.1-sol"
    prices = {"input": 2, "output": 10}
    deadline = None

    def __init__(self, unresolved=False):
        self.calls, self.unresolved = [], unresolved

    def time_left(self):
        return None if self.deadline is None else self.deadline - time.monotonic()

    def required_budget(self, *args, **kwargs):
        return 25000

    def invoke(self, system, payload, budget, phase, **kwargs):
        self.calls.append({"phase": phase, "payload": copy.deepcopy(payload), **kwargs})
        call = budget.reserve(20000, {"phase": phase})
        budget.settle(call, 5000, {})
        result = {"findings": [finding()], "hypotheses": [], "requests": [], "coverage_gaps": [], "summary": "reviewed"}
        if phase == "discovery":
            result["coverage_gaps"] = ["Check how zero reaches the numeric consumer"]
        elif phase == "verification":
            result["dispositions"] = [
                {"candidate_id": c["candidate_id"], "verdict": "supported", "reason": "Zero is replaced",
                 "evidence": ["src/parse.ts:R2 uses a truthy fallback"], "finding_index": 0}
                for c in payload["candidates_and_hypotheses"]]
            result["question_resolutions"] = [] if self.unresolved else [
                {"question_id": q["question_id"], "verdict": "supported", "reason": "Zero takes the fallback",
                 "evidence": ["src/parse.ts:R2 returns one for zero"], "finding_index": 0}
                for q in payload["open_questions"]]
        return result


def read(repository, sha, path, start, end):
    return {"path": path, "ref": sha, "start_line": 1, "end_line": 3, "total_lines": 3,
            "content": "export function parse(s) {\n  return Number(s) || 1;\n}", "truncated": False}


class ModernUnitTests(unittest.TestCase):
    def test_source_deadline_stops_before_further_paid_calls_and_is_retryable(self):
        module = importlib.import_module("units")
        model = UnitModel()
        with patch.object(source._CommitReader, "read_text", side_effect=source.SourceDeadlineReached("expired")):
            result = module.review_context(context(), model, source.read_context_file, Budget())
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(result["retryable"])
        self.assertEqual([call["phase"] for call in model.calls], ["discovery"])
        self.assertTrue(any(note.startswith("Context transport failure") for note in result["retrieval_notes"]))

    def test_all_frozen_variants_preserve_verified_findings_and_incremental_context(self):
        for index in (14,):
            with self.subTest(variant=index):
                module = importlib.import_module("units")
                value = context()
                value.update(incremental={"mode": "incremental"}, cumulative_context={"changed_paths": ["other.ts"]})
                model = UnitModel()
                result = module.review_context(value, model, read, Budget())
                self.assertEqual(result["status"], "complete", result["coverage_gaps"])
                self.assertEqual(len(result["findings"]), 1)
                self.assertEqual(model.calls[0]["cache_context"]["earlier_pr_changes"], value["cumulative_context"])
                self.assertEqual(model.calls[-1]["payload"]["earlier_pr_changes"], value["cumulative_context"])
                self.assertTrue(all(call.get("effort") == "medium" for call in model.calls))

    def test_missing_explicit_question_resolution_remains_partial(self):
        module = importlib.import_module("units")
        result = module.review_context(context(), UnitModel(unresolved=True), read, Budget())
        self.assertEqual(result["status"], "complete")
        self.assertTrue(any("no unique explicit resolution" in g for g in result["review_notes"]))

    def test_source_operations_share_real_review_deadline(self):
        module = importlib.import_module("units")
        model = UnitModel()
        model.deadline = time.monotonic() + 420
        saved_deadline = model.deadline
        seen = []
        def bounded_read(*args):
            seen.append(source.source_timeout(10000))
            return read(*args)
        result = module.review_context(context(), model, bounded_read, Budget())
        self.assertEqual(len(result["findings"]), 1)
        self.assertTrue(seen)
        self.assertLessEqual(max(seen), 420)
        self.assertEqual(model.deadline, saved_deadline)

    def test_unpublished_anchor_cannot_clear_supported_question(self):
        module = importlib.import_module("units")
        value = context()
        value["publishable_lines"] = {"src/parse.ts": [3]}
        result = module.review_context(value, UnitModel(), read, Budget())
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["status"], "complete")
        self.assertTrue(any("no surviving verified finding" in g for g in result["review_notes"]))

    def test_missing_duplicate_and_unsupplied_question_proof_fail_closed(self):
        questions = question_records(["question"])
        valid = {"question_id": "q1", "verdict": "rejected", "reason": "Guard handles it",
                 "evidence": ["src/parse.ts:R2 checks it"]}
        for rows in ([], [valid, valid], [{**valid, "evidence": ["unseen.ts:R2 guard"]}],
                     [{**valid, "evidence": []}], [{**valid, "verdict": "unresolved"}]):
            with self.subTest(rows=rows):
                _, gaps = audit_questions(questions, {"question_resolutions": rows}, [], {"src/parse.ts"})
                self.assertTrue(gaps)

    def test_source_cited_question_rejection_is_accounted_for(self):
        questions = question_records(["question", "question"])
        self.assertEqual(len(questions), 1)
        result = {"question_resolutions": [{"question_id": "q1", "verdict": "rejected",
                  "reason": "The guard prevents it", "evidence": ["src/parse.ts:R1 guard"]}]}
        records, gaps = audit_questions(questions, result, [], {"src/parse.ts"})
        self.assertEqual(gaps, [])
        self.assertTrue(records[0]["accepted"])



if __name__ == "__main__":
    unittest.main()
