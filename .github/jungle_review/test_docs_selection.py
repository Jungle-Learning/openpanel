"""Deterministic documentation selection (registry schema 2). Synthetic files only: no corpus input, no network."""
import hashlib
import json
import random
import shutil
import tempfile
import unittest
from pathlib import Path

import docs_evidence
from docs_evidence import DocRegistry, admit_doc_requests, select_documents

DOCS = Path(docs_evidence.__file__).resolve().parent / "docs"


def patch(*changed, context=()):
    lines = ["@@ -1,3 +1,3 @@", *(f" {line}" for line in context), *changed]
    return "\n".join(lines) + "\n"


MODAL_FILE = {"path": "worker/jobs.py", "head_content": "import modal\napp = modal.App()\n",
              "patch": patch("+@app.function(retries=0)", "-@app.function(retries=3)", context=("import modal",))}
FIRESTORE_FILE = {"path": "web/src/store.ts", "head_content": "import { runTransaction } from 'firebase/firestore';\n",
                  "patch": patch("+  await runTransaction(db, async (tx) => {")}
WORKFLOW_FILE = {"path": ".github/workflows/review.yml", "patch": patch("+  pull_request_target:")}


def select(files, admitted=None, registry=None):
    admitted = [] if admitted is None else admitted
    log = []
    entries = select_documents(registry or DocRegistry(), files, admitted, log)
    return [e["request"]["doc"] for e in entries], admitted, log


class RegistryTests(unittest.TestCase):
    def copy(self, change):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        shutil.copytree(DOCS, directory / "docs")
        manifest = json.loads((directory / "docs" / "registry.json").read_text())
        change(manifest)
        (directory / "docs" / "registry.json").chmod(0o644)
        (directory / "docs" / "registry.json").write_text(json.dumps(manifest))
        return DocRegistry(directory / "docs")

    def test_the_committed_registry_is_schema_2_with_every_snapshot_verified(self):
        registry = DocRegistry()
        self.assertEqual((registry.schema, registry.config_error, registry.unavailable), (2, None, {}))
        self.assertEqual(len(registry.records), 8)
        historical = [d for d, r in registry.records.items() if r["kind"] == "historical"]
        self.assertTrue(historical and all(not registry.relevance[d]["auto"] for d in historical))

    def test_a_schema_1_registry_still_loads_and_never_auto_selects(self):
        def to_schema_1(manifest):
            manifest["schema"] = 1
            for doc in manifest["docs"]:
                doc.pop("relevance", None)
            manifest.pop("relevance_policy", None)
        registry = self.copy(to_schema_1)
        self.assertIsNone(registry.config_error)
        self.assertEqual(len(registry.records), 8)
        self.assertEqual(select([MODAL_FILE], registry=registry)[0], [])

    def test_malformed_or_unbounded_relevance_rejects_the_whole_registry(self):
        def historical_auto(m):
            next(d for d in m["docs"] if d["kind"] == "historical")["relevance"] = {
                "auto": True, "family": "x", "ecosystem": {"paths": [{"name": "a", "regex": "a"}]},
                "surface": [{"name": "b", "regex": "b"}]}

        def too_many(m):
            m["docs"][0]["relevance"]["surface"] = [{"name": f"p{i}", "regex": "x"} for i in range(13)]

        def bad_regex(m):
            m["docs"][0]["relevance"]["surface"] = [{"name": "bad", "regex": "("}]

        def long_regex(m):
            m["docs"][0]["relevance"]["surface"] = [{"name": "long", "regex": "x" * 201}]

        def no_gate(m):
            m["docs"][0]["relevance"]["ecosystem"] = {}

        def missing(m):
            m["docs"][0].pop("relevance")

        def schema_3(m):
            m["schema"] = 3

        def unknown_scope(m):
            m["docs"][0]["relevance"]["scope"] = "everywhere"

        def runtime_without_caveat(m):
            next(d for d in m["docs"] if d["id"] == "modal.retries")["relevance"].pop("runtime_caveat")

        def runtime_path_gate(m):
            next(d for d in m["docs"] if d["id"] == "modal.retries")["relevance"]["ecosystem"]["paths"] = [
                {"name": "any", "regex": "."}]
        for change in (historical_auto, too_many, bad_regex, long_regex, no_gate, missing, schema_3, unknown_scope,
                       runtime_without_caveat, runtime_path_gate):
            with self.subTest(change.__name__):
                registry = self.copy(change)
                self.assertIsNotNone(registry.config_error)
                self.assertEqual(registry.records, {})

    def test_a_tampered_snapshot_is_never_selected(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        shutil.copytree(DOCS, directory / "docs")
        target = directory / "docs" / "modal.retries.txt"
        target.chmod(0o644)
        target.write_text(target.read_text() + "x")
        registry = DocRegistry(directory / "docs")
        self.assertIn("modal.retries", registry.unavailable)
        picked, _, _ = select([MODAL_FILE], registry=registry)
        self.assertNotIn("modal.retries", picked)


class SelectionTests(unittest.TestCase):
    def test_api_scope_needs_the_gate_and_a_changed_surface_construct(self):
        picked, admitted, log = select([FIRESTORE_FILE])
        self.assertEqual(picked, ["firestore.transactions"])
        self.assertEqual(admitted, picked)
        self.assertEqual((log[0]["status"], log[0]["basis"], log[0]["score"], log[0]["signals"]),
                         ("auto_selected", "api", 1, ["runTransaction"]))

    def test_an_api_construct_only_in_unchanged_context_selects_nothing(self):
        file = {"path": "web/src/store.ts", "head_content": "import { runTransaction } from 'firebase/firestore';\n",
                "patch": patch("+  const x = 1;", context=("  await runTransaction(db, async (tx) => {",))}
        self.assertEqual(select([file])[0], [])

    def test_no_vendor_gate_means_no_selection(self):
        file = {"path": "lib/util.py", "patch": patch("+def run(retries=3):", "+    return fn.remote()",
                                                      "+    await runTransaction(db, f)")}
        self.assertEqual(select([file])[0], [])

    def test_for_api_scope_the_gate_and_the_surface_must_be_in_the_same_file(self):
        gate_only = {"path": "a.ts", "patch": patch("+import { getDoc } from 'firebase/firestore';")}
        surface_only = {"path": "b.ts", "patch": patch("+  await runTransaction(db, f)")}
        self.assertEqual(select([gate_only, surface_only])[0], [])

    def test_runtime_scope_selects_on_a_changed_vendor_file_without_any_surface_change(self):
        body_only = {"path": "worker/jobs.py", "head_content": "import modal\napp = modal.App()\n",
                     "patch": patch("+    total = compute(batch)", "-    total = compute(items)",
                                    context=("@app.function(retries=0)", "def run(batch):"))}
        picked, _, log = select([body_only])
        self.assertEqual(picked, ["modal.retries", "modal.preemption"])   # one family; the unused slot is filled
        self.assertEqual([entry["basis"] for entry in log], ["runtime", "runtime"])
        self.assertEqual(log[0]["signals"], ["vendor runtime import in a changed file"])

    def test_runtime_entries_carry_the_relevance_caveat(self):
        registry = DocRegistry()
        entries = select_documents(registry, [MODAL_FILE], [], [])
        for entry in entries:
            selection = entry["result"]["selection"]
            self.assertEqual(selection["basis"], "runtime")
            self.assertIn("relevance signal, not proof that the changed code executes inside a Modal Function",
                          selection["caveat"])
            self.assertIn("Untrusted evidence", entry["result"]["framing"])
        self.assertNotIn("selection", registry.entry("modal.retries")[0]["result"])   # requested entries unchanged

    def test_runtime_scope_needs_the_vendor_import_in_a_changed_file(self):
        unrelated = {"path": "worker/other.py", "patch": patch("+x = 1")}   # no Modal import visible in this file
        self.assertEqual(select([unrelated])[0], [])

    def test_only_the_two_modal_snapshots_are_runtime_scoped(self):
        registry = DocRegistry()
        runtime = sorted(d for d, r in registry.relevance.items() if r.get("auto") and r["scope"] == "runtime")
        self.assertEqual(runtime, ["modal.preemption", "modal.retries"])

    def test_workflow_path_gate_selects_the_current_github_snapshot_never_the_historical_one(self):
        picked, _, _ = select([WORKFLOW_FILE])
        self.assertEqual(picked, ["github.events.pull_request_target"])
        non_workflow = {"path": "docs/notes.yml", "patch": patch("+  pull_request_target:")}
        self.assertEqual(select([non_workflow])[0], [])

    def test_one_per_family_first_then_at_most_two_in_total(self):
        picked, _, log = select([MODAL_FILE, FIRESTORE_FILE, WORKFLOW_FILE])
        self.assertEqual(len(picked), 2)
        self.assertEqual(len({log_entry["family"] for log_entry in log if log_entry["status"] == "auto_selected"}), 2)
        self.assertTrue(all(entry["status"] in ("auto_selected", "auto_eligible_not_selected") for entry in log))

    def test_the_result_does_not_depend_on_file_order(self):
        files = [MODAL_FILE, FIRESTORE_FILE, WORKFLOW_FILE,
                 {"path": "web/src/App.tsx", "head_content": "import React from 'react';\n",
                  "patch": patch("+  useEffect(() => {", "+  useEffect(() => {")}]
        expected = select(files)
        generator = random.Random(7)
        for _ in range(20):
            shuffled = files[:]
            generator.shuffle(shuffled)
            self.assertEqual(select(shuffled), expected)

    def test_a_document_matching_several_files_ranks_by_its_earliest_path_whatever_the_arrival_order(self):
        # firestore matches b/x.ts and y/z.ts (score 2); react matches c/app.tsx twice (score 2): a tie on score.
        firestore_b = dict(FIRESTORE_FILE, path="b/x.ts")
        firestore_y = dict(FIRESTORE_FILE, path="y/z.ts")
        react_c = {"path": "c/app.tsx", "head_content": "import React from 'react';\n",
                   "patch": patch("+  useEffect(() => {", "+  useEffect(() => {")}
        expected = ["firestore.transactions", "react.strict-mode"]          # b/x.ts sorts before c/app.tsx
        for order in ([firestore_y, react_c, firestore_b], [react_c, firestore_y, firestore_b],
                      [firestore_b, firestore_y, react_c]):
            self.assertEqual(select(order)[0], expected)

    def test_ties_break_by_canonical_path_then_registry_order(self):
        stripe_late = {"path": "z/hooks.ts", "head_content": "import Stripe from 'stripe';\n",
                       "patch": patch("+  const event = stripe.webhooks.constructEvent(body, sig, secret);")}
        firestore_early = dict(FIRESTORE_FILE, path="a/store.ts")
        picked, _, _ = select([stripe_late, firestore_early])
        self.assertEqual(picked[0], "firestore.transactions")    # same score (1), earlier path wins

    def test_auto_picks_share_the_two_slots_with_requests(self):
        self.assertEqual(select([MODAL_FILE], admitted=["stripe.webhooks"])[0], ["modal.retries"])
        self.assertEqual(select([MODAL_FILE], admitted=["stripe.webhooks", "react.strict-mode"])[0], [])
        picked, admitted, _ = select([MODAL_FILE, FIRESTORE_FILE])
        log = []
        entries, notes = admit_doc_requests(DocRegistry(), [{"kind": "doc", "doc": "stripe.webhooks"}], admitted,
                                            log, 1, auto_selected=tuple(picked))
        self.assertEqual(entries, [])
        self.assertEqual(log[0]["status"], "over_limit")
        self.assertEqual(log[0]["cause"], "auto_selected")
        self.assertIn("Documentation stripe.webhooks not supplied: the 2 document slots were filled", notes[0])
        self.assertIn("by automatic selection (unverified)", notes[0])

    def test_an_auto_picked_document_is_not_supplied_twice_when_requested(self):
        picked, admitted, _ = select([WORKFLOW_FILE])
        log = []
        entries, notes = admit_doc_requests(DocRegistry(), [{"kind": "doc", "doc": picked[0]}], admitted, log, 1,
                                            auto_selected=tuple(picked))
        self.assertEqual((entries, notes, log[0]["status"]), ([], [], "already_supplied"))

    def test_entries_keep_framing_hash_and_version_caveat_and_logs_carry_no_pr_text(self):
        registry = DocRegistry()
        entries = select_documents(registry, [MODAL_FILE], [], log := [])
        for entry in entries:
            result = entry["result"]
            self.assertIn("Untrusted evidence", result["framing"])
            self.assertIn("does not by itself prove how an older version behaved", result["framing"])
            self.assertEqual(hashlib.sha256(result["content"].encode()).hexdigest(), result["excerpt_sha256"])
        self.assertNotIn("retries=0", json.dumps(log))

    def test_line_count_and_length_are_bounded(self):
        many = dict(FIRESTORE_FILE, patch=patch(*(["+  await runTransaction(db, f)"]
                                                  * (docs_evidence.MAX_CHANGED_LINES_PER_FILE + 50))))
        _, _, log = select([many])
        self.assertEqual(log[0]["score"], docs_evidence.MAX_CHANGED_LINES_PER_FILE)
        long_line = dict(FIRESTORE_FILE, patch=patch("+" + "x" * (docs_evidence.MAX_LINE_CHARS + 10) + " runTransaction(db)"))
        self.assertEqual(select([long_line])[0], [])           # the construct lies beyond the read bound

    def test_nothing_is_selected_without_files_or_when_the_registry_is_disabled(self):
        self.assertEqual(select([])[0], [])
        registry = DocRegistry()
        registry.config_error = "disabled"
        self.assertEqual(select([MODAL_FILE], registry=registry)[0], [])


if __name__ == "__main__":
    unittest.main()
