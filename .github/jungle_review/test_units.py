"""evidence-v24 (units): deterministic routing, unit audits and the mandatory final verification. Fake models only."""
import json
import unittest

import focused
import prompts
import units
from docs_evidence import DocRegistry
from provider import Budget, HARD_CAP_MICRODOLLARS, InvalidModelOutput, ReviewUnavailable
from test_focused import FakeModel, answer, inventory, reads


def added(lines):
    return "@@ -0,0 +1,%d @@\n" % len(lines) + "".join(f"+{line}\n" for line in lines)


def modified(old, new):
    return "@@ -1,%d +1,%d @@\n" % (len(old), len(new)) + "".join(f"-{l}\n" for l in old) + "".join(f"+{l}\n" for l in new)


def record(path, patch, status="modified", **extra):
    return {"path": path, "status": status, "patch": patch, "changed_lines": [], **extra}


def sample_inventory(files=None, **overrides):
    files = files if files is not None else [
        record("src/panel.ts", added(["import { load } from './store';", "export function panel(s) {",
                                      "  if (!s) return 'loading';", "  return load(s);", "}"]), status="added"),
        record("src/store.ts", modified(["export const load = (s) => s;"], ["export const load = (s) => s.rows;"])),
        record("src/other.py", modified(["def f():", "    return 1"], ["def f():", "    return 2"])),
        record("tests/ui/app.playwright.config.ts", modified(["port: 1"], ["port: 2"])),
        record("tests/test_panel.spec.ts", added(["import { panel } from '../src/panel';", "expect(panel(1));"]),
               status="added"),
        record("tests/fixtures/data.json", added(['{"a": 1}']), status="added"),
        record("docs/guide.md", modified(["old"], ["new"])),
        record("assets/logo.png", None, status="added"),
    ]
    return inventory(files=files, context_files=[], **overrides)


HYP = {"path": "src/panel.ts", "trigger": "s is null", "mechanism": "renders loading forever",
       "check": "src/panel.ts:R3 null state"}


class UnitModel(FakeModel):
    """FakeModel with a fixed per-call price, so budget scenarios are exact."""
    def __init__(self, answers, unit_cost=20_000, verification_cost=30_000, followup_cost=10_000):
        super().__init__(answers)
        self.costs = {"discovery": unit_cost, "verification": verification_cost, "investigation": followup_cost}

    def required_budget(self, system, payload, phase, **kwargs):
        return self.costs[phase]


def run(model, context=None, budget=None, **kwargs):
    return units.review_units(context or sample_inventory(), model, reads(), budget=budget, **kwargs)


class RoutingTests(unittest.TestCase):
    def test_kinds_keep_executable_configuration_primary_even_under_test_paths(self):
        self.assertEqual(units.route_kind("src/a.ts"), "source")
        self.assertEqual(units.route_kind("tests/ui/x.playwright.config.ts"), "config")
        self.assertEqual(units.route_kind(".github/workflows/ci.yml"), "config")
        self.assertEqual(units.route_kind("package.json"), "config")
        self.assertEqual(units.route_kind("tests/conftest.py"), "config")
        self.assertEqual(units.route_kind("tests/test_a.py"), "test")
        self.assertEqual(units.route_kind("src/a.spec.ts"), "test")
        self.assertEqual(units.route_kind("tests/fixtures/x.json"), "fixture")
        self.assertEqual(units.route_kind("tests/fixtures/x-fixture-server.mjs"), "test")  # executable code
        self.assertEqual(units.route_kind("docs/a.md"), "doc")
        self.assertEqual(units.route_kind("data/table.csv"), "data")

    def test_connected_imports_form_one_unit_and_roles_order_primary_first(self):
        routing = units.route(sample_inventory())
        # Connected files share a unit (small independent units may be packed with them).
        self.assertTrue(any({"src/panel.ts", "src/store.ts"} <= set(u) for u in routing["units"]))
        roles = routing["roles"]
        self.assertEqual(roles, sorted(roles, key=lambda r: r != "primary"))  # primary units first
        primary = [p for u, r in zip(routing["units"], roles) if r == "primary" for p in u]
        self.assertIn("tests/ui/app.playwright.config.ts", primary)
        deferred = [p for u, r in zip(routing["units"], roles) if r == "deferred" for p in u]
        self.assertEqual(sorted(deferred), ["docs/guide.md", "tests/fixtures/data.json", "tests/test_panel.spec.ts"])
        self.assertEqual(routing["unreviewable"], ["assets/logo.png"])
        # every changed file is routed exactly once or named as unreviewable
        routed = [p for u in routing["units"] for p in u]
        self.assertEqual(sorted(routed + routing["unreviewable"]),
                         sorted(f["path"] for f in sample_inventory()["files"]))
        self.assertEqual(len(routed), len(set(routed)))

    def test_python_relative_imports_connect(self):
        files = [record("pkg/a.py", modified(["x = 1"], ["from .b import y", "x = y"])),
                 record("pkg/b.py", modified(["y = 1"], ["y = 2"]))]
        self.assertEqual(units.route(inventory(files=files, context_files=[]))["units"], [["pkg/a.py", "pkg/b.py"]])

    def test_routing_is_deterministic_and_order_independent(self):
        forward = units.route(sample_inventory())
        files = list(reversed(sample_inventory()["files"]))
        self.assertEqual(units.route(sample_inventory(files=files)), forward)

    def test_tests_only_change_is_promoted_to_primary_units(self):
        files = [record("tests/test_a.py", modified(["assert 1"], ["assert 2"])),
                 record("tests/fixtures/x.json", added(["{}"]), status="added")]
        routing = units.route(inventory(files=files, context_files=[]))
        self.assertTrue(routing["promoted"])
        self.assertEqual(set(routing["roles"]), {"primary"})

    def test_deleted_code_follows_the_file_that_takes_it_over(self):
        body = [f"const handleExamSelection{n} = renderExamChoice{n}(state);" for n in range(25)]
        files = [record("src/old.tsx", "@@ -1,25 +0,0 @@\n" + "".join(f"-{l}\n" for l in body), status="removed"),
                 record("src/new.tsx", added(body), status="added"),
                 record("src/unrelated.ts", modified(["a"], ["b"]))]
        routing = units.route(inventory(files=files, context_files=[]))
        self.assertTrue(any({"src/new.tsx", "src/old.tsx"} <= set(u) for u in routing["units"]))
        connected = units.import_edges({f["path"]: f for f in files})
        self.assertEqual(connected["src/old.tsx"], set())  # the takeover link comes from shared identifiers

    def test_large_components_split_at_the_unit_bound(self):
        big = ["x" * 200] * 200  # about 40 k characters each
        files = [record("src/a.ts", added(["import './b';", *big]), status="added"),
                 record("src/b.ts", added(big), status="added")]
        routing = units.route(inventory(files=files, context_files=[]))
        self.assertEqual(len(routing["units"]), 2)

    def test_renamed_without_patch_is_noted_and_missing_patch_is_unreviewable(self):
        files = [record("src/a.ts", None, status="renamed", old_path="src/z.ts"),
                 record("src/b.ts", None), record("src/c.ts", modified(["a"], ["b"]))]
        routing = units.route(inventory(files=files, context_files=[]))
        self.assertEqual(routing["unreviewable"], ["src/b.ts"])
        self.assertTrue(any("renamed from src/z.ts" in n for n in routing["notes"]))

    def test_policy_exclusions_remain_named_without_becoming_missing_source(self):
        files = [record("src/a.ts", modified(["return 1"], ["return 2"])),
                 record("yarn.lock", None), record("assets/logo.png", None),
                 record("src/unavailable.ts", None)]
        data = sample_inventory(files=files)
        data["coverage"]["excluded_files"] = [
            {"path": "yarn.lock", "reason": "lockfile"},
            {"path": "assets/logo.png", "reason": "binary_extension"},
        ]
        routing = units.route(data)
        self.assertEqual(routing["unreviewable"], ["src/unavailable.ts"])
        for path in ("yarn.lock", "assets/logo.png"):
            self.assertTrue(any(path in note and "excluded" in note for note in routing["notes"]))
        evidence = units.unit_evidence(data, routing, 0)
        routes = {f["path"]: f["route"] for f in evidence["changed_files"]}
        self.assertIn("excluded", routes["yarn.lock"])
        self.assertIn("not reviewable", routes["src/unavailable.ts"])

    def test_an_explicit_source_omission_wins_over_an_inconsistent_exclusion(self):
        data = sample_inventory(files=[record("src/a.ts", None, status="renamed", old_path="src/old.ts")])
        data["coverage"]["excluded_files"] = [{"path": "src/a.ts", "reason": "generated"}]
        data["coverage"]["omitted_files"] = [{"path": "src/a.ts", "reason": "no_patch_from_api"}]
        self.assertEqual(units.route(data)["unreviewable"], ["src/a.ts"])


class InstructionTests(unittest.TestCase):
    def test_unit_instruction_uses_the_trusted_schema_and_generic_contract_rule(self):
        for field in ('"findings"', '"hypotheses"', '"requests"', '"coverage_gaps"', '"summary"'):
            self.assertIn(field, prompts.POLICY)
        text = units.UNIT_AUDIT
        self.assertIn("When control.phase is discovery for a unit audit", text)
        self.assertIn("never against the difference itself", text)
        self.assertIn("an intended change of behavior is not a defect", text)
        self.assertIn("positive state", text)
        self.assertIn("negative state", text)
        self.assertIn("request the producer or a caller", text)
        self.assertIn("never describe it as comprehensive", text)
        self.assertIn("at most 3 requests", text)
        # evidence-v24: follow-ups may request (bounded); the last round's requests are answered for verification.
        self.assertIn("make at most 3 new requests", units.UNIT_FOLLOWUP)
        self.assertIn("answered for the final verification", units.UNIT_FOLLOWUP)
        self.assertNotRegex(text + units.UNIT_FOLLOWUP, r"[A-Za-z0-9_-]+/[A-Za-z0-9_./-]+\.[a-z]{1,4}")

    def test_unit_calls_carry_the_documentation_catalog_and_paths_only_in_evidence(self):
        model = UnitModel([answer()] * 8)
        run(model)
        first = model.calls[0]
        self.assertEqual(first["phase"], "discovery")
        self.assertIn(units.UNIT_AUDIT, first["instruction"])
        self.assertIn("Fixed official documentation snapshots are available by ID only", first["instruction"])
        self.assertNotIn("src/panel.ts", first["instruction"])
        changed = {e["path"]: e["route"] for e in first["cache_context"]["changed_files"]}
        self.assertEqual(set(changed), {f["path"] for f in sample_inventory()["files"]})  # full inventory


class BudgetTests(unittest.TestCase):
    def test_primary_ceiling_preserves_later_units_and_the_final_floor(self):
        model = UnitModel([answer()] * 10)
        result = run(model)
        floor = round(result["units"]["final_floor_usd"] * 1e6)
        primary = [u for u in result["units"]["units"] if u["role"] == "primary"]
        self.assertEqual(len(primary), 2)
        self.assertEqual(model.calls[0]["ceiling"], HARD_CAP_MICRODOLLARS * 0 + 410_000 - 20_000 - floor)
        self.assertLessEqual(result["budget"]["cost_upper_bound_usd"], 0.41)
        self.assertEqual(result["model_cap_usd"], 0.41)

    def test_deferred_units_run_after_primaries_and_unaudited_ones_are_named(self):
        # Enough for the primaries and the floor, not for every deferred unit.
        model = UnitModel([answer()] * 10, unit_cost=60_000)
        result = run(model, budget=Budget(cap=230_000))
        log = result["units"]["units"]
        audited_roles = [u["role"] for u in log if u["audited"]]
        self.assertEqual(audited_roles[:2], ["primary", "primary"])
        self.assertTrue(any(u["role"] == "deferred" and not u["audited"] for u in log))
        self.assertTrue(any(g.startswith("deferred unit") and "not audited (budget)" in g for g in result["coverage_gaps"]))
        self.assertEqual(result["status"], "partial")
        self.assertLessEqual(result["budget"]["cost_upper_bound_usd"], 0.23)

    def test_no_primary_unit_fits_sends_nothing(self):
        model = UnitModel([answer()], unit_cost=400_000)
        result = run(model)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(model.calls, [])
        self.assertTrue(any("not audited (budget)" in g for g in result["coverage_gaps"]))

    def test_mandatory_final_that_cannot_fit_fails_closed(self):
        model = UnitModel([answer([dict(focused_finding())])] + [answer()] * 9, verification_cost=600_000)
        result = run(model)
        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn("verification", [c["phase"] for c in model.calls])


def focused_finding():
    return {"path": "src/panel.ts", "line": 3, "priority": "P1", "title": "Return a loaded state",
            "body": "b", "confidence": 0.9, "evidence": ["src/panel.ts:R3"], "counterargument": "none"}


class ResultTests(unittest.TestCase):
    def test_review_completes_with_explicit_policy_exclusions_but_not_missing_source(self):
        files = [record("src/a.ts", modified(["export const a = 1;"], ["export const a = 2;"])),
                 record("yarn.lock", None)]
        data = sample_inventory(files=files)
        data["coverage"]["excluded_files"] = [{"path": "yarn.lock", "reason": "lockfile"}]
        final = dict(answer(), dispositions=[{"candidate_id": "c1", "verdict": "rejected",
                    "reason": "The changed constant preserves its contract", "evidence": ["src/a.ts:R1 result"]}])
        result = run(UnitModel([answer(), final]), data)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["coverage_gaps"], [])
        self.assertTrue(any("yarn.lock" in note and "excluded" in note for note in result["scope_notes"]))
        # A missing source patch without a collector policy exclusion still fails closed.
        data["coverage"]["excluded_files"] = []
        result = run(UnitModel([answer(), final]), data)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(any("yarn.lock" in note for note in result["coverage_gaps"]))

    def test_unusable_unit_result_is_a_named_gap_and_the_review_continues(self):
        model = UnitModel([{"findings": "x", "summary": "s"}] + [answer(hypotheses=[HYP])] + [answer()] * 8)
        result = run(model)
        self.assertNotEqual(result["status"], "unavailable")
        self.assertTrue(any("returned an unusable result (findings is not an array)" in g for g in result["coverage_gaps"]))
        self.assertIn("verification", [c["phase"] for c in model.calls])

    def test_settled_invalid_output_is_a_unit_gap_but_unknown_billing_stops(self):
        class Failing(UnitModel):
            def __init__(self, answers, billing_known, error):
                super().__init__(answers)
                self.billing_known, self.error, self.failed = billing_known, error, False

            def invoke(self, system, payload, budget, phase, **kwargs):
                if not self.failed:
                    self.failed = True
                    call = budget.reserve(20_000, {"phase": phase})
                    call["billing_known"] = self.billing_known
                    self.calls.append({"phase": phase})
                    raise self.error
                return super().invoke(system, payload, budget, phase, **kwargs)

        known = Failing([answer()] * 10, True, InvalidModelOutput("Reviewer returned invalid JSON"))
        result = run(known)
        self.assertNotEqual(result["status"], "unavailable")
        self.assertTrue(any("output unusable (Reviewer returned invalid JSON)" in g for g in result["coverage_gaps"]))
        self.assertEqual(result["units"]["units"][0]["budget_call"], 0)
        for billing_known, error in ((False, ReviewUnavailable("Missing provider usage; full reservation retained")),
                                     (True, ReviewUnavailable("Provider served a premium tier that was not requested")),
                                     (False, InvalidModelOutput("unknown billing despite the class"))):
            with self.subTest(error=str(error)):
                unknown = Failing([answer()] * 10, billing_known, error)
                budget = Budget(cap=410_000)
                stopped = run(unknown, budget=budget)
                self.assertEqual(stopped["status"], "unavailable")
                self.assertEqual(len(unknown.calls), 1)
                self.assertGreaterEqual(budget.spent, 20_000)  # the failed paid call stays counted
                self.assertEqual(stopped["units"]["units"][0]["budget_call"], 0)

    def test_overflow_is_named_never_silent(self):
        many = answer(findings=[dict(focused_finding(), title=f"t{n}") for n in range(7)],
                      hypotheses=[dict(HYP, trigger=f"h{n}") for n in range(10)],
                      requests=[{"kind": "read", "path": "src/store.ts", "ref": "head", "start": n, "end": n + 1}
                                for n in range(1, 6)],
                      gaps=[f"q{n}" for n in range(8)])
        model = UnitModel([many] + [answer()] * 12)
        result = run(model)
        text = "\n".join(result["coverage_gaps"])
        self.assertIn("unit 1: 2 provisional finding(s) beyond the unit limit of 5", text)
        self.assertIn("unit 1: 2 hypothesis(es) beyond the unit limit of 8", text)
        self.assertIn("unit 1: 2 evidence request(s) beyond the unit limit of 3", text)
        # evidence-v24: every open question is carried to the final payload (exact budget decides).
        verification = [c for c in model.calls if c["phase"] == "verification"][0]
        self.assertIn("unit 1: q7", json.dumps(verification["payload"]["open_questions"]))

    def test_candidates_beyond_the_envelope_are_named(self):
        long = "z" * 3000
        heavy = answer(hypotheses=[dict(HYP, trigger=f"h{n}", mechanism=long) for n in range(8)])
        model = UnitModel([heavy, heavy] + [answer()] * 10)
        result = run(model)
        self.assertTrue(any(g.startswith("candidate not carried to verification (envelope full)")
                            for g in result["coverage_gaps"]))
        self.assertLessEqual(len(json.dumps(result["audit"]["items"])), focused.CANDIDATE_LIST_MAX_CHARS + 2000)

    def test_followup_updates_lead_but_omitted_earlier_candidates_are_kept(self):
        first = answer(hypotheses=[HYP, dict(HYP, trigger="second state")],
                       requests=[{"kind": "read", "path": "src/store.ts", "ref": "head", "start": 1, "end": 2}])
        followup = answer(hypotheses=[dict(HYP, check="updated check")])  # restates one, omits the other
        model = UnitModel([first, answer(), followup] + [answer()] * 8)
        result = run(model)
        triggers = [(i.get("trigger"), i.get("check")) for i in result["audit"]["items"]]
        self.assertIn(("s is null", "updated check"), triggers)
        self.assertIn("second state", [t for t, _ in triggers])
        verification = [c for c in model.calls if c["phase"] == "verification"][0]
        self.assertIn("second state", json.dumps(verification["payload"]))

    def test_documentation_requests_use_the_fixed_registry_and_the_review_limit(self):
        registry = DocRegistry()
        ids = sorted(registry.records)
        self.assertGreaterEqual(len(ids), 3)
        first = answer(hypotheses=[HYP], requests=[{"kind": "doc", "doc": ids[0]}, {"kind": "doc", "doc": "invented"}])
        second = answer(hypotheses=[dict(HYP, path="src/other.py")],
                        requests=[{"kind": "doc", "doc": ids[1]}, {"kind": "doc", "doc": ids[2]}])
        model = UnitModel([first, second, answer(), answer()] + [answer()] * 8)
        result = run(model, doc_registry=registry)
        self.assertEqual(len(result["documentation"]["supplied"]), 2)
        statuses = {(e["doc"], e["status"]) for e in result["documentation"]["requests"]}
        self.assertTrue(any(s != "admitted" for _, s in statuses))
        verification = [c for c in model.calls if c["phase"] == "verification"][0]
        self.assertIn(ids[0], json.dumps(verification["cache_context"]))


class VerificationTests(unittest.TestCase):
    def test_zero_candidates_get_a_second_look_at_audited_code_never_an_empty_package(self):
        model = UnitModel([answer()] * 10)
        result = run(model)
        verification = [c for c in model.calls if c["phase"] == "verification"]
        self.assertEqual(len(verification), 1)
        self.assertEqual(result["units"]["verification"], "second look")
        self.assertTrue(verification[0]["cache_context"]["hunks"])  # real audited code, not an empty package
        self.assertTrue(all(i["kind"] == "second-look" for i in result["audit"]["items"]))

    def test_a_second_look_that_does_not_fit_is_skipped_without_a_paid_call(self):
        class Tight(UnitModel):
            def required_budget(self, system, payload, phase, **kwargs):
                if phase == "verification" and payload.get("candidates_and_hypotheses"):
                    return 400_000  # the second look would not fit; the empty floor does
                return super().required_budget(system, payload, phase, **kwargs)
        model = Tight([answer()] * 10)
        result = run(model)
        self.assertNotIn("verification", [c["phase"] for c in model.calls])
        self.assertEqual(result["units"]["verification"], "not run: the second look did not fit the remaining budget")
        self.assertTrue(any("optional second-look verification did not fit" in n for n in result["scope_notes"]))
        self.assertNotEqual(result["status"], "unavailable")

    def test_a_small_fully_audited_change_can_be_complete(self):
        files = [record("src/a.ts", modified(["a"], ["b"]))]
        final = dict(answer(), dispositions=[{"candidate_id": "c1", "verdict": "rejected",
                    "reason": "The changed result preserves its contract", "evidence": ["src/a.ts:R1 result"]}])
        model = UnitModel([answer(), final])
        result = run(model, context=inventory(files=files, context_files=[]))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["units"]["verification"], "second look")

    def test_deleted_files_are_audited_and_named_as_unanchorable(self):
        files = [record("src/gone.ts", "@@ -1,2 +0,0 @@\n-a\n-b\n", status="removed"),
                 record("src/a.ts", modified(["a"], ["b"]))]
        model = UnitModel([answer()] * 4)
        result = run(model, context=inventory(files=files, context_files=[]))
        self.assertTrue(any("src/gone.ts: no head line is shown" in g for g in result["coverage_gaps"]))
        audited = [p for u in result["units"]["units"] if u["audited"] for p in u["files"]]
        self.assertIn("src/gone.ts", audited)


class ProviderOutputClassTests(unittest.TestCase):
    def transport(self, response):
        def send(url, headers, body, timeout=180):
            if url.endswith("input_tokens"):
                return {"input_tokens": 50}
            return {"service_tier": body.get("service_tier"), **response}
        return send

    def invoke(self, response):
        import os
        import provider
        os.environ.setdefault("OPENAI_API_KEY", "test-only")
        budget = Budget(cap=100_000)
        model = provider.Model("gpt-6.1-sol", transport=self.transport(response))
        try:
            model.invoke("s", {}, budget, "discovery", max_output=1500, minimum_output=1200)
        except ReviewUnavailable as error:
            return error, budget
        return None, budget

    def test_settled_unusable_output_is_invalid_model_output_and_unknown_usage_is_not(self):
        usage = {"input_tokens": 50, "output_tokens": 10, "input_tokens_details": {"cached_tokens": 0}}
        cases = {
            "invalid json": {"status": "completed", "usage": usage,
                             "output": [{"type": "message", "content": [{"type": "output_text", "text": "{"}]}]},
            "non-object": {"status": "completed", "usage": usage,
                           "output": [{"type": "message", "content": [{"type": "output_text", "text": "[1]"}]}]},
            "incomplete": {"status": "incomplete", "usage": usage, "output": []},
            "malformed content": {"status": "completed", "usage": usage, "output": ["text"]},
        }
        for name, response in cases.items():
            with self.subTest(case=name):
                error, budget = self.invoke(response)
                self.assertIsInstance(error, InvalidModelOutput)
                self.assertTrue(budget.calls[-1]["billing_known"])
        error, budget = self.invoke({"status": "completed", "output": []})  # no usage: billing unknown
        self.assertNotIsInstance(error, InvalidModelOutput)
        self.assertFalse(budget.calls[-1]["billing_known"])


class FinalFitTests(unittest.TestCase):
    def test_candidates_that_do_not_fit_are_named_and_trimmed_before_failing_closed(self):
        class Shrinking(UnitModel):
            def required_budget(self, system, payload, phase, **kwargs):
                if phase == "verification":
                    count = len(payload.get("candidates_and_hypotheses", []))
                    return 30_000 + max(0, count - 2) * 400_000  # only two candidates fit
                return super().required_budget(system, payload, phase, **kwargs)
        hypotheses = [dict(HYP, trigger=f"state {n}") for n in range(4)]
        model = Shrinking([answer(hypotheses=hypotheses)] + [answer()] * 9)
        result = run(model)
        self.assertNotEqual(result["status"], "unavailable")
        dropped = [g for g in result["coverage_gaps"] if g.startswith("candidate not carried to verification (final budget)")]
        self.assertEqual(len(dropped), 2)
        self.assertEqual(len(result["audit"]["items"]), 2)

    def test_deferred_units_never_take_what_verifying_existing_candidates_needs(self):
        class Costly(UnitModel):
            def required_budget(self, system, payload, phase, **kwargs):
                if phase == "verification" and payload.get("candidates_and_hypotheses"):
                    return 340_000  # verifying the primary candidates needs most of the cap
                return super().required_budget(system, payload, phase, **kwargs)
        model = Costly([answer(hypotheses=[HYP])] + [answer()] * 9)
        result = run(model)
        log = result["units"]["units"]
        self.assertFalse(any(u["role"] == "deferred" and u["audited"] for u in log))
        self.assertTrue(any(g.startswith("deferred unit") for g in result["coverage_gaps"]))
        self.assertIn("verification", [c["phase"] for c in model.calls])
        self.assertLessEqual(result["budget"]["cost_upper_bound_usd"], 0.41)

    def test_context_snippets_beyond_the_unit_bound_are_named(self):
        context = {**sample_inventory(), "context_files": [
            {"path": f"src/caller{n}.ts", "ref": "head", "content": "x" * 3000, "reason": "caller of src/store.ts"}
            for n in range(4)]}
        model = UnitModel([answer()] * 10)
        result = units.review_units(context, model, reads())
        # evidence-v24 (M6): the envelope omission is a scope note, and the snippet is offered to verification.
        self.assertTrue(any("collector snippet src/caller3.ts" in n and "offered to the final verification" in n
                            for n in result["scope_notes"]))
        self.assertFalse(any("caller3" in g for g in result["coverage_gaps"]))
        self.assertGreaterEqual(result["units"]["reads"]["snippets_offered"], 4)


class DurableBudgetCapTests(unittest.TestCase):
    def test_top_level_model_cap_equals_the_durable_report_never_the_all_in_cap(self):
        import provider
        import publish
        allowance = provider.RUNNER_ALLOWANCE_MICRODOLLARS
        for attempts, prior_model in ((1, 0), (2, 50_000)):
            with self.subTest(attempts=attempts):
                records = []
                runner = attempts * allowance
                budget = publish.DurableBudget(lambda kind, rid, amount: records.append((kind, rid, amount)),
                                               "9.1", spent=runner + prior_model, runner_reserved=runner)
                result = run(UnitModel([answer()] * 10), budget=budget)
                report = result["budget"]
                self.assertEqual(result["model_cap_usd"], report["model_cap_usd"])
                self.assertEqual(result["model_cap_usd"], (provider.ALL_IN_CAP_MICRODOLLARS - runner) / 1e6)
                self.assertNotEqual(result["model_cap_usd"], provider.ALL_IN_CAP_MICRODOLLARS / 1e6)
                self.assertEqual(report["scope_cap_usd"], provider.ALL_IN_CAP_MICRODOLLARS / 1e6)
                self.assertLessEqual(report["model_cost_upper_bound_usd"], report["model_cap_usd"])
                self.assertTrue(records)  # reservations reached the ledger before sends
        self.assertEqual(provider.ALL_IN_CAP_MICRODOLLARS - allowance, provider.HARD_CAP_MICRODOLLARS)

    def test_unavailable_results_carry_the_same_model_cap(self):
        import provider
        import publish
        budget = publish.DurableBudget(lambda *a: None, "9.2", spent=provider.RUNNER_ALLOWANCE_MICRODOLLARS,
                                       runner_reserved=provider.RUNNER_ALLOWANCE_MICRODOLLARS)
        result = run(UnitModel([answer()], unit_cost=450_000), budget=budget)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["model_cap_usd"], result["budget"]["model_cap_usd"])
        self.assertEqual(result["model_cap_usd"], provider.HARD_CAP_MICRODOLLARS / 1e6)


class RobustnessTests(unittest.TestCase):
    def test_an_unexpected_exception_after_reserving_keeps_the_paid_record(self):
        class Broken(UnitModel):
            def invoke(self, system, payload, budget, phase, **kwargs):
                budget.reserve(23_456, {"phase": phase})
                self.calls.append({"phase": phase})
                raise TypeError("secret provider text that must not be echoed")
        model = Broken([])
        budget = Budget(cap=410_000)
        result = run(model, budget=budget)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["findings"], [])
        self.assertEqual(budget.spent, 23_456)
        self.assertEqual(result["budget"]["cost_upper_bound_usd"], 0.023456)
        self.assertIn("Reviewer failed unexpectedly (TypeError)", result["coverage_gaps"])
        self.assertEqual(result["model_cap_usd"], result["budget"]["model_cap_usd"])
        self.assertNotIn("secret provider text", json.dumps(result))
        self.assertEqual(len(model.calls), 1)  # no retry, no continuation


class TimeHoldTests(unittest.TestCase):
    class Clocked(UnitModel):
        """The integrated provider's deadline behavior on a fake clock: a call starts only with at least
        COUNT_TIMEOUT_SECONDS + MIN_USEFUL_RESPONSE_SECONDS (105 s) left before this model's deadline, else
        DeadlineReached is raised with nothing sent; a started call advances the clock by `seconds`."""
        def __init__(self, answers, seconds, deadline, now=0.0):
            super().__init__(answers)
            self.seconds, self.now, self.deadline, self.started = seconds, now, deadline, []

        def invoke(self, system, payload, budget, phase, **kwargs):
            import provider
            if self.deadline - self.now < provider.COUNT_TIMEOUT_SECONDS + provider.MIN_USEFUL_RESPONSE_SECONDS:
                raise provider.DeadlineReached("Review deadline reached before a model request; no request sent")
            self.started.append((phase, self.now, self.deadline))
            self.now += self.seconds(phase) if callable(self.seconds) else self.seconds
            return super().invoke(system, payload, budget, phase, **kwargs)

    def test_constants_match_the_provider_and_root_allocation(self):
        import provider
        self.assertEqual(units.VERIFICATION_COUNT_SECONDS, provider.COUNT_TIMEOUT_SECONDS)
        self.assertEqual(units.VERIFICATION_MIN_USEFUL_SECONDS, provider.MIN_USEFUL_RESPONSE_SECONDS)
        self.assertEqual(units.FINAL_TIME_HOLD_SECONDS, 135)
        self.assertEqual(units.UNITS_MODEL_CAP_MICRODOLLARS, provider.HARD_CAP_MICRODOLLARS)
        self.assertEqual(provider.REVIEW_DEADLINE_SECONDS, 420)

    def test_optional_calls_use_the_earlier_deadline_and_verification_the_real_one(self):
        model = self.Clocked([answer()] * 12, seconds=50, deadline=420.0)
        result = run(model)
        optional = [(p, d) for p, _, d in model.started if p != "verification"]
        self.assertTrue(optional)
        self.assertEqual({d for _, d in optional}, {420.0 - 135})
        self.assertEqual([d for p, _, d in model.started if p == "verification"], [420.0])
        self.assertEqual(model.deadline, 420.0)  # restored once, though model is also the verifier
        self.assertNotEqual(result["status"], "unavailable")

    def test_an_optional_long_response_never_takes_the_verification_time(self):
        # The first unit's response takes 200 s: no other optional call may start, verification still runs.
        model = self.Clocked([answer()] * 12, seconds=lambda phase: 200 if phase == "discovery" else 30,
                             deadline=420.0)
        result = run(model)
        self.assertEqual([p for p, _, _ in model.started], ["discovery", "verification"])
        self.assertTrue(any("not audited (time)" in g for g in result["coverage_gaps"]))
        self.assertEqual(result["units"]["stopped_by_time_at_unit"], 2)
        self.assertEqual(result["units"]["verification"], "second look")  # the final call still ran

    def test_no_optional_call_starts_with_only_the_minimum_left_globally(self):
        # 200 s left globally (more than the 105 s a call needs) is still less than 135 + 105: nothing optional.
        model = self.Clocked([answer()] * 4, seconds=10, deadline=420.0, now=220.0)
        result = run(model)
        self.assertNotIn("discovery", [p for p, _, _ in model.started])
        self.assertTrue(all("(time)" in g or "not audited" not in g for g in result["coverage_gaps"]))
        self.assertTrue(any("primary unit 1 not audited (time)" in g for g in result["coverage_gaps"]))
        self.assertEqual(model.deadline, 420.0)

    def test_a_separate_verifier_keeps_its_own_deadline_restored(self):
        model = self.Clocked([answer()] * 10, seconds=10, deadline=420.0)
        verifier = self.Clocked([answer()] * 2, seconds=10, deadline=400.0)
        units.review_units(sample_inventory(), model, reads(), verifier=verifier)
        self.assertEqual((model.deadline, verifier.deadline), (420.0, 400.0))
        self.assertTrue(all(d == 400.0 for p, _, d in verifier.started if p == "verification"))

    def test_the_real_deadline_is_restored_when_the_review_stops_early(self):
        model = self.Clocked([answer()], seconds=10, deadline=420.0)
        model.costs["discovery"] = 400_000  # no primary unit fits: stops before any generation
        result = run(model)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(model.deadline, 420.0)

    def test_fakes_without_a_deadline_are_unchanged(self):
        model = UnitModel([answer()] * 10)
        result = run(model)
        self.assertFalse(hasattr(model, "deadline"))
        self.assertIsNone(result["units"]["stopped_by_time_at_unit"])
        self.assertFalse(any("(time)" in g for g in result["coverage_gaps"]))


if __name__ == "__main__":
    unittest.main()
