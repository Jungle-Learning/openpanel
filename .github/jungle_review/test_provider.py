import json
import os
import unittest
from unittest.mock import patch

from provider import HARD_CAP_MICRODOLLARS, Budget, BudgetExceeded, Model, ReviewUnavailable, parse_json


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test", "OPENAI_API_KEY": "test"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def transport(self, input_count=20000, output=1000, fail=False, incomplete=False):
        calls = []
        def send(url, headers, body, timeout=180):
            calls.append((url, body))
            if url.endswith(("count_tokens", "input_tokens")):
                return {"input_tokens": input_count}
            if fail:
                raise ReviewUnavailable("Connection lost after send")
            usage = {"input_tokens": input_count, "output_tokens": output}
            if "anthropic.com" in url:
                return {"content": [{"type": "text", "text": '{"findings":[]}'}],
                        "stop_reason": "max_tokens" if incomplete else "end_turn", "usage": usage}
            return {"output": [{"type": "message", "content": [{"type": "output_text", "text": '{"findings":[]}'}]}],
                    "status": "incomplete" if incomplete else "completed", "service_tier": "default", "usage": usage}
        return send, calls

    def test_cap_cannot_be_raised(self):
        for cap in [HARD_CAP_MICRODOLLARS + 1, 500001, 0, -1, float("nan"), True]:
            with self.assertRaises(ValueError):
                Budget(cap=cap)

    def test_reservation_precedes_generation(self):
        budget = Budget()
        def send(url, headers, body, timeout=180):
            if url.endswith(("count_tokens", "input_tokens")):
                self.assertEqual(budget.spent, 0)
                return {"input_tokens": 100}
            self.assertGreater(budget.spent, 0)
            raise ReviewUnavailable("Timeout")
        with self.assertRaises(ReviewUnavailable):
            Model(transport=send).invoke("system", {}, budget, "discovery")
        self.assertGreater(budget.spent, 0)
        self.assertFalse(budget.report()["billing_complete"])

    def test_multiple_calls_and_retries_share_cap(self):
        send, calls = self.transport(fail=True)
        model, budget = Model(transport=send), Budget()
        for _ in range(8):
            with self.assertRaises(ReviewUnavailable):
                model.invoke("system", {}, budget, "retry")
        generation = [b for u, b in calls if not u.endswith(("count_tokens", "input_tokens"))]
        self.assertLessEqual(budget.spent, HARD_CAP_MICRODOLLARS)
        self.assertLessEqual(len(generation), 3)

    def test_preflight_refuses_expensive_input_without_generation(self):
        send, calls = self.transport(input_count=180000)
        budget = Budget()
        with self.assertRaises(BudgetExceeded):
            Model(transport=send).invoke("system", {}, budget, "discovery")
        self.assertEqual(len(calls), 1)
        self.assertEqual(budget.spent, 0)

    def test_actual_usage_releases_unused_reservation(self):
        send, calls = self.transport()
        budget = Budget()
        result = Model("claude-opus-5-5", transport=send).invoke("system", {}, budget, "discovery")
        self.assertEqual(result, {"findings": []})
        self.assertEqual(budget.spent, 100000)
        self.assertEqual(calls[-1][1]["thinking"], {"type": "adaptive"})
        self.assertEqual(calls[-1][1]["output_config"]["effort"], "medium")

    def test_reasoning_exhaustion_is_paid_and_never_clean(self):
        send, _ = self.transport(incomplete=True)
        budget = Budget()
        with self.assertRaises(ReviewUnavailable):
            Model(transport=send).invoke("system", {}, budget, "discovery")
        self.assertEqual(budget.spent, 50000)
        self.assertTrue(budget.report()["billing_complete"])

    def test_sol_counts_reasoning_and_requests_json(self):
        send, calls = self.transport()
        budget = Budget()
        Model("gpt-6.1-sol", transport=send).invoke("system", {}, budget, "discovery")
        self.assertEqual(budget.spent, 50000)
        self.assertEqual(calls[-1][1]["reasoning"]["effort"], "medium")
        self.assertIn("JSON", calls[-1][1]["input"])
        self.assertFalse(calls[-1][1]["store"])

    def test_free_preflight_sizes_full_verification_before_spending(self):
        send, calls = self.transport(input_count=20000)
        model = Model('gpt-6.1-sol', send)
        expected = (22000 + 1024) * 2 * 1.25 + 6000 * 10
        needed = model.required_budget('system', {}, 'verification', output_tokens=6000)
        self.assertEqual(needed, expected)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][0].endswith('input_tokens'))

    def test_high_discovery_preserves_effort_and_counts_all_output(self):
        send, calls = self.transport()
        budget = Budget()
        Model('gpt-6.1-sol', send).invoke('system', {}, budget, 'discovery', effort='high')
        self.assertEqual(calls[-1][1]['reasoning']['effort'], 'high')
        self.assertEqual(budget.report()['calls'][0]['effort'], 'high')
        self.assertEqual(budget.spent, 50000)

    def test_verification_refuses_tiny_response_before_generation(self):
        send, calls = self.transport(input_count=20000)
        with self.assertRaises(BudgetExceeded):
            Model('gpt-6.1-sol', send).invoke('system', {}, Budget(cap=70000),
                'verification', minimum_output=4000)
        self.assertEqual(len(calls), 1)

    def test_no_unknown_model_or_fallback_pricing(self):
        with self.assertRaises(ValueError):
            Model("latest")

    def test_sol_cache_write_cost_is_included(self):
        def send(url, headers, body, timeout=180):
            if url.endswith("input_tokens"):
                return {"input_tokens": 10000}
            return {"status": "completed", "service_tier": "default", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": '{"ok":true}'}]}],
                "usage": {"input_tokens": 10000, "output_tokens": 100,
                          "input_tokens_details": {"cache_write_tokens": 8000, "cached_tokens": 1000,
                              "cache_write_tokens_details": {"ttl_30m_tokens": 8000, "ttl_12h_tokens": 0}}}}
        budget = Budget()
        Model("gpt-6.1-sol", send, cache_prefix=True).invoke("system", {}, budget, "test", cache_context={"code": "x"})
        self.assertEqual(budget.spent, 23100)

    def test_anthropic_caches_identical_context_between_passes(self):
        send, calls = self.transport(input_count=2000)
        model, budget = Model("claude-opus-5-5", transport=send), Budget()
        context = {"files": ["same immutable input"]}
        model.invoke("same system", {}, budget, "discovery", cache_context=context)
        model.invoke("same system", {"proposals": []}, budget, "verification", cache_context=context)
        generations = [b for u,b in calls if u.endswith("/messages")]
        self.assertEqual(generations[0]["system"], generations[1]["system"])
        self.assertEqual(generations[0]["messages"][0]["content"][0], generations[1]["messages"][0]["content"][0])
        self.assertEqual(generations[0]["messages"][0]["content"][0]["cache_control"]["ttl"], "5m")

    def test_malformed_output_is_not_coerced_to_success(self):
        for text in ["[]", "null", "{} trailing", 'prefix {"findings":[]}']:
            with self.assertRaises(ReviewUnavailable):
                parse_json(text)


if __name__ == "__main__":
    unittest.main()
