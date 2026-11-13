"""Budget allocation regressions; fake models, no paid or network calls."""
import unittest

import focused
from provider import Budget
from test_focused import FakeModel, answer, inventory, reads


class OpusModel(FakeModel):
    model = "claude-opus-5-5"
    prices = {"input": 4, "cached": 0.2, "output": 20}


def medium_change():
    # A bounded diff that fits together with a useful final response. The former
    # fixed maximum-envelope estimate withheld it before any candidate existed.
    patch = "@@ -0,0 +1,1800 @@\n" + "+value = update(value)\n" * 1800
    return inventory(files=[{"path": "new.py", "status": "added", "patch": patch,
                             "changed_lines": list(range(1, 1801))}], context_files=[])


class BalancedAdmissionTests(unittest.TestCase):
    def test_opus_keeps_an_affordable_diff_without_prespending_on_unknown_evidence(self):
        model = OpusModel([answer(), answer()], cost_per_char=2)
        result = focused.review_context(medium_change(), model, reads(), budget=Budget())
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["discovery_level"], 0)
        self.assertTrue(model.calls[0]["cache_context"]["files"][0]["patch"])
        self.assertEqual([c["phase"] for c in model.calls], ["discovery", "verification"])
        self.assertLessEqual(result["budget"]["cost_upper_bound_usd"], 0.50)

    def test_unexpected_final_evidence_cost_stops_before_an_unaffordable_generation(self):
        class ExpensiveVerifier(OpusModel):
            def required_budget(self, system, payload, phase, **kwargs):
                if phase == "verification":
                    return 400_000
                return super().required_budget(system, payload, phase, **kwargs)

        model = ExpensiveVerifier([answer()], cost_per_char=2)
        result = focused.review_context(medium_change(), model, reads(), budget=Budget())
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual([c["phase"] for c in model.calls], ["discovery"])
        self.assertTrue(any("mandatory final verification cannot fit" in g
                            for g in result["coverage_gaps"]))
        self.assertLessEqual(result["budget"]["cost_upper_bound_usd"], 0.50)


if __name__ == "__main__":
    unittest.main()
