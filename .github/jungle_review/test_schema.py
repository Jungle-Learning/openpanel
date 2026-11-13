"""schema-v18 contract: the request schema the model is given (system POLICY) and the controller's
request parsers agree exactly, for every kind, including documentation requests."""
import json
import re
import unittest
from unittest import mock

import context
import docs_evidence
import focused
import prompts
from review import retrieve
from test_focused import FINDING, FakeModel, answer, inventory, reads

PROBE_KINDS = ("read", "search", "find_path", "doc", "fetch", "url", "web", "docs", "documentation", "")


def schema_request() -> dict:
    """The requests element of the system response schema, parsed as JSON."""
    match = re.search(r'"requests":\[(\{.*?\})\]', prompts.POLICY, re.S)
    assert match, "system POLICY has no requests schema"
    return json.loads(match.group(1))


def schema_kinds() -> set:
    return set(schema_request()["kind"].split("|"))


def example(kind: str) -> dict:
    """A request built only from the schema's own field names and example values."""
    template = schema_request()
    if kind == docs_evidence.DOC_KIND:
        return {"kind": kind, "doc": sorted(docs_evidence.DocRegistry().records)[0]}
    # An enumerated example value ("head|base") stands for one of its alternatives: take the first.
    request = {key: (value.split("|")[0] if isinstance(value, str) and "|" in value else value)
               for key, value in template.items() if key != "doc"}
    request["kind"] = kind
    return request


def accepted_by_controller(kind: str) -> bool:
    """True when the controller's real parsers answer this request instead of rejecting it."""
    request = example(kind) if kind in schema_kinds() else {**example("read"), "kind": kind}
    if docs_evidence.is_doc_request(request):
        entries, notes = docs_evidence.admit_doc_requests(docs_evidence.DocRegistry(), [request], [], [], 0)
        return bool(entries) and not notes
    current = inventory()
    with mock.patch.object(context, "search_context_file", return_value={"match_count": 0}), \
            mock.patch.object(context, "discover_file_paths", return_value={"paths": [], "match_count": 0}):
        extra, notes = retrieve({"requests": [request]}, current, reads())
    return bool(extra) and not notes


class SchemaContractTests(unittest.TestCase):
    def test_the_system_schema_lists_exactly_the_kinds_the_controller_answers(self):
        accepted = {kind for kind in PROBE_KINDS if accepted_by_controller(kind)}
        self.assertEqual(accepted, schema_kinds())
        self.assertIn(docs_evidence.DOC_KIND, schema_kinds())
        self.assertTrue({"read", "search", "find_path"} <= schema_kinds())  # every earlier kind retained

    def test_the_schema_names_the_field_the_documentation_parser_reads(self):
        self.assertIn("doc", schema_request())
        doc_id = sorted(docs_evidence.DocRegistry().records)[0]
        entries, _ = docs_evidence.admit_doc_requests(docs_evidence.DocRegistry(),
                                                      [{"kind": "doc", "doc": doc_id}], [], [], 0)
        self.assertEqual(entries[0]["request"], {"kind": "doc", "doc": doc_id})
        # Every source field the schema shows is still a field retrieve() reads.
        self.assertEqual(set(schema_request()) - {"doc"}, {"kind", "path", "ref", "start", "end", "query"})

    def test_the_instruction_example_conforms_to_the_system_schema(self):
        shown = re.search(r'(\{"kind":"doc","doc":"<id>"\})', focused.DOCUMENTATION_ADDENDUM)
        self.assertIsNotNone(shown)
        request = json.loads(shown.group(1))
        self.assertLessEqual(set(request), set(schema_request()))
        self.assertIn(request["kind"], schema_kinds())

    def test_the_model_receives_the_schema_and_the_ids_it_refers_to_in_every_requesting_phase(self):
        class Recording(FakeModel):
            def invoke(self, system, payload, budget, phase, **kwargs):
                self.systems = getattr(self, "systems", []) + [(phase, system)]
                return super().invoke(system, payload, budget, phase, **kwargs)
        model = Recording([answer([FINDING], requests=[{"kind": "read", "path": "app.py", "ref": "head",
                                                         "start": 1, "end": 5}]),
                           answer([FINDING]), answer([FINDING])])
        focused.review_context(inventory(), model, reads())
        catalog_ids = sorted(docs_evidence.DocRegistry().records)
        for (phase, system), call in zip(model.systems, model.calls):
            self.assertIn('"kind":"read|search|find_path|doc"', system, phase)
            if phase in ("discovery", "investigation"):
                self.assertIn("Available IDs:", call["instruction"])
                self.assertTrue(all(doc_id in call["instruction"] for doc_id in catalog_ids))
        self.assertEqual([phase for phase, _ in model.systems], ["discovery", "investigation", "verification"])

    def test_a_schema_shaped_doc_request_is_supplied_end_to_end(self):
        doc_id = sorted(docs_evidence.DocRegistry().records)[0]
        model = FakeModel([answer([FINDING], requests=[example("doc")]), answer([FINDING]), answer([FINDING])])
        outcome = focused.review_context(inventory(), model, reads())
        self.assertEqual(outcome["documentation"]["supplied"], [doc_id])
        self.assertIn(doc_id, outcome["documentation"]["in_final_package"])


if __name__ == "__main__":
    unittest.main()
