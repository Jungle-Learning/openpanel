"""Service-tier contracts (Flex adapter, carried over from the frozen flex smoke) plus publisher
wiring and diagnostics. Fake transports only: no network."""
import json
import math
import unittest

import provider
import publish
from provider import FAST_PRICING, FLEX_PRICING, PRICING, Budget, Model, ReviewUnavailable

INPUT_TOKENS = 400
_ENVIRONMENT = None


def setUpModule():
    """A placeholder key for the fake transports (never sent anywhere), like test_provider.py."""
    global _ENVIRONMENT
    from unittest.mock import patch
    _ENVIRONMENT = patch.dict("os.environ", {"OPENAI_API_KEY": "test-placeholder"})
    _ENVIRONMENT.start()


def tearDownModule():
    _ENVIRONMENT.stop()


class Transport:
    """Counts token preflight requests and generations; returns a scripted generation."""

    def __init__(self, served="flex", output_tokens=300, status="completed", error=None, omit_tier=False):
        self.served, self.output_tokens, self.status, self.error, self.omit_tier = (
            served, output_tokens, status, error, omit_tier)
        self.generations, self.bodies, self.timeouts = 0, [], []

    def __call__(self, url, headers, body, timeout=180):
        if url.endswith("/input_tokens"):
            return {"input_tokens": INPUT_TOKENS}
        self.generations += 1
        self.bodies.append(body)
        self.timeouts.append(timeout)
        if self.error is not None:
            raise self.error
        response = {"status": self.status, "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps({"sum": 0})}]}],
            "usage": {"input_tokens": INPUT_TOKENS, "output_tokens": self.output_tokens,
                      "input_tokens_details": {"cached_tokens": 0}}}
        if not self.omit_tier:
            response["service_tier"] = self.served
        return response


def expected(rates, output_tokens):
    return math.ceil(INPUT_TOKENS * rates["input"] + output_tokens * rates["output"])


def invoke(tier="flex", **transport_options):
    transport = Transport(**transport_options)
    model = Model("gpt-6.1-sol", transport=transport, service_tier=tier)
    budget = Budget(cap=30_000)
    return model, budget, transport


class RequestTests(unittest.TestCase):
    def test_only_default_and_flex_can_be_requested_and_flex_only_where_verified(self):
        for tier in ("auto", "priority", "fast", "scale", "ultrafast", ""):
            with self.assertRaises(ValueError):
                Model("gpt-6.1-sol", transport=Transport(), service_tier=tier)
        with self.assertRaises(ValueError):
            Model("claude-opus-5-5", transport=Transport(), service_tier="flex")
        for timeout in (0, 181, True, 30.0):
            with self.assertRaises(ValueError):
                Model("gpt-6.1-sol", transport=Transport(), request_timeout=timeout)
        self.assertEqual(Model("gpt-6.1-sol", transport=Transport()).service_tier, "flex")       # production default
        self.assertEqual(Model("claude-opus-5-5", transport=Transport()).service_tier, "default")
        self.assertEqual(Model("gpt-6.1-sol", transport=Transport(), service_tier="default").service_tier, "default")

    def test_the_requested_tier_store_false_and_timeout_reach_the_request_once(self):
        for tier in ("flex", "default"):
            model, budget, transport = invoke(tier, served=tier)
            model.invoke("s", {}, budget, "smoke", max_output=1500)
            self.assertEqual(transport.generations, 1)
            self.assertEqual((transport.bodies[0]["service_tier"], transport.bodies[0]["store"]), (tier, False))
            self.assertEqual(transport.timeouts, [180])

    def test_reservations_are_at_standard_rates_whatever_tier_is_requested(self):
        reserved = []
        for tier in ("default", "flex"):
            model, budget, transport = invoke(tier, error=ReviewUnavailable("Provider HTTP 429"))
            with self.assertRaises(ReviewUnavailable):
                model.invoke("s", {}, budget, "smoke", max_output=1500)
            reserved.append(budget.spent)
        self.assertEqual(reserved[0], reserved[1])
        input_ceiling = math.ceil((math.ceil(INPUT_TOKENS * 1.10) + 1024) * PRICING["gpt-6.1-sol"]["input"] * 1.25)
        self.assertEqual(reserved[0], input_ceiling + 1500 * PRICING["gpt-6.1-sol"]["output"])


class SettlementTests(unittest.TestCase):
    def test_a_confirmed_flex_receipt_settles_at_flex_prices(self):
        model, budget, _ = invoke("flex", served="flex")
        model.invoke("s", {}, budget, "smoke", max_output=1500)
        call = budget.calls[0]
        self.assertEqual(budget.spent, expected(FLEX_PRICING["gpt-6.1-sol"], 300))
        self.assertEqual((call["billing_known"], call["service_tier_served"]), (True, "flex"))
        self.assertTrue(budget.report()["billing_complete"])

    def test_a_default_receipt_settles_at_standard_prices_even_when_flex_was_requested(self):
        model, budget, _ = invoke("flex", served="default")
        model.invoke("s", {}, budget, "smoke", max_output=1500)
        self.assertEqual(budget.spent, expected(PRICING["gpt-6.1-sol"], 300))

    def test_a_priority_or_fast_receipt_is_recorded_at_fast_prices_and_stops(self):
        for served in ("priority", "fast"):
            with self.subTest(served=served):
                model, budget, _ = invoke("flex", served=served)
                with self.assertRaisesRegex(ReviewUnavailable, "premium tier"):
                    model.invoke("s", {}, budget, "smoke", max_output=1500)
                self.assertEqual(budget.spent, expected(FAST_PRICING["gpt-6.1-sol"], 300))
                self.assertTrue(budget.calls[0]["billing_known"])

    def test_a_fast_receipt_above_the_reservation_is_counted_in_full_and_stops(self):
        model, budget, _ = invoke("flex", served="fast", output_tokens=1500)
        with self.assertRaisesRegex(ReviewUnavailable, "exceeded reservation"):
            model.invoke("s", {}, budget, "smoke", max_output=1500)
        self.assertEqual(budget.spent, expected(FAST_PRICING["gpt-6.1-sol"], 1500))
        self.assertFalse(budget.report()["billing_complete"])

    def test_a_priority_receipt_above_the_reservation_is_counted_in_full_and_stops(self):
        model, budget, _ = invoke("flex", served="priority", output_tokens=1500)
        reserved = None
        with self.assertRaisesRegex(ReviewUnavailable, "exceeded reservation"):
            model.invoke("s", {}, budget, "smoke", max_output=1500)
        reserved = round(budget.calls[0]["reserved_usd"] * 1e6)
        actual = expected(FAST_PRICING["gpt-6.1-sol"], 1500)
        self.assertGreater(actual, reserved)            # the standard reservation did not cover the premium
        self.assertEqual(budget.spent, actual)          # the overrun is counted, not hidden
        self.assertEqual(budget.calls[0]["actual_usd"], actual / 1e6)
        self.assertFalse(budget.report()["billing_complete"])

    def test_an_unknown_or_missing_tier_is_unsettled_unknown_and_stops(self):
        for served, omit in (("auto", False), ("scale", False), ("ultrafast", False), (5, False), (None, True)):
            with self.subTest(served=served):
                model, budget, _ = invoke("flex", served=served, omit_tier=omit)
                with self.assertRaisesRegex(ReviewUnavailable, "billing unknown"):
                    model.invoke("s", {}, budget, "smoke", max_output=1500)
                call = budget.calls[0]
                self.assertEqual(budget.spent, round(call["reserved_usd"] * 1e6))   # reservation retained
                self.assertEqual((call["billing_known"], call["billing"]), (False, "unknown_service_tier"))
                self.assertFalse(budget.report()["billing_complete"])

    def test_http_429_and_timeouts_keep_the_reservation_and_are_never_retried(self):
        for error in (ReviewUnavailable("Provider HTTP 429"), ReviewUnavailable("Provider HTTP 408"),
                      ReviewUnavailable("Provider transport/format error: TimeoutError")):
            with self.subTest(error=str(error)):
                model, budget, transport = invoke("flex", error=error)
                with self.assertRaises(ReviewUnavailable):
                    model.invoke("s", {}, budget, "smoke", max_output=1500)
                self.assertEqual(transport.generations, 1)
                self.assertEqual(budget.spent, round(budget.calls[0]["reserved_usd"] * 1e6))
                self.assertFalse(budget.calls[0]["billing_known"])

    def test_an_incomplete_flex_response_is_settled_then_stops(self):
        model, budget, _ = invoke("flex", served="flex", status="incomplete")
        with self.assertRaisesRegex(ReviewUnavailable, "did not finish"):
            model.invoke("s", {}, budget, "smoke", max_output=1500)
        self.assertEqual(budget.spent, expected(FLEX_PRICING["gpt-6.1-sol"], 300))


class MalformedPaidOutputTests(unittest.TestCase):
    """A paid, settled response whose output shape is wrong fails closed with its usage kept, never a raw
    exception that loses the ledger evidence, and never with response text in the message."""

    OPENAI_SHAPES = {"output not a list": {"output": "x"}, "item not an object": {"output": ["x"]},
                     "content not a list": {"output": [{"type": "message", "content": "x"}]},
                     "part not an object": {"output": [{"type": "message", "content": ["x"]}]},
                     "text not a string": {"output": [{"type": "message", "content": [{"type": "output_text",
                                                                                      "text": 5}]}]}}

    def test_openai_malformed_output_after_settlement(self):
        for label, change in self.OPENAI_SHAPES.items():
            with self.subTest(label):
                transport = Transport(served="flex")
                original = transport.__call__

                def send(url, headers, body, timeout=180, original=original, change=change):
                    response = original(url, headers, body, timeout)
                    return {**response, **change} if isinstance(response, dict) and "usage" in response else response
                model = Model("gpt-6.1-sol", transport=send)
                budget = Budget(cap=30_000)
                with self.assertRaises(ReviewUnavailable) as raised:
                    model.invoke("s", {}, budget, "smoke", max_output=1500)
                self.assertEqual(str(raised.exception), "Malformed provider output; usage settled")
                self.assertTrue(budget.calls[0]["billing_known"])
                self.assertEqual(budget.spent, expected(FLEX_PRICING["gpt-6.1-sol"], 300))

    def test_anthropic_malformed_content_after_settlement(self):
        import os
        from unittest.mock import patch
        usage = {"input_tokens": 400, "output_tokens": 300}
        for label, content in (("not a list", "x"), ("block not an object", ["x"]),
                               ("text not a string", [{"type": "text", "text": 5}])):
            with self.subTest(label):
                def send(url, headers, body, timeout=180, content=content):
                    if url.endswith("/count_tokens"):
                        return {"input_tokens": 400}
                    return {"stop_reason": "end_turn", "usage": usage, "content": content}
                with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-placeholder"}):
                    budget = Budget(cap=100_000)
                    with self.assertRaises(ReviewUnavailable) as raised:
                        Model("claude-opus-5-5", transport=send).invoke("s", {}, budget, "smoke", max_output=1500)
                self.assertEqual(str(raised.exception), "Malformed provider output; usage settled")
                self.assertTrue(budget.calls[0]["billing_known"])
                self.assertEqual(budget.spent, math.ceil(400 * 4 + 300 * 20))


def openai_send(text="{}", status="completed", served="flex", usage=None, output=None, error=None):
    def send(url, headers, body, timeout=180):
        if url.endswith("/input_tokens"):
            return {"input_tokens": INPUT_TOKENS}
        if error is not None:
            raise error
        response = {"status": status, "service_tier": served,
                    "usage": usage if usage is not None else {"input_tokens": INPUT_TOKENS, "output_tokens": 300,
                                                              "input_tokens_details": {"cached_tokens": 0}},
                    "output": output if output is not None else
                    [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}
        return response
    return send


class OutputClassificationTests(unittest.TestCase):
    """Only an unusable answer whose charge was settled at an expected tier is InvalidModelOutput (a caller may
    mark that unit incomplete and continue). Everything else stays ReviewUnavailable and must stop."""

    def outcome(self, send, budget=None):
        budget = budget or Budget(cap=30_000)
        try:
            Model("gpt-6.1-sol", transport=send).invoke("s", {}, budget, "unit", max_output=1500)
        except ReviewUnavailable as error:
            return error, budget
        return None, budget

    def test_settled_but_unusable_answers_are_invalid_model_output(self):
        for label, send in (("invalid JSON", openai_send(text="not json")),
                            ("non-object JSON", openai_send(text="[1, 2]")),
                            ("incomplete", openai_send(status="incomplete")),
                            ("malformed shape", openai_send(output=["x"])),
                            ("served default, unusable", openai_send(text="not json", served="default"))):
            with self.subTest(label):
                error, budget = self.outcome(send)
                self.assertIsInstance(error, provider.InvalidModelOutput, label)
                self.assertTrue(budget.calls[0]["billing_known"])
                self.assertEqual(budget.calls[0]["output"], "invalid")
                self.assertTrue(budget.report()["billing_complete"])

    def test_every_must_stop_failure_is_not_invalid_model_output(self):
        overrun = {"input_tokens": INPUT_TOKENS, "output_tokens": 1500, "input_tokens_details": {"cached_tokens": 0}}
        for label, send in (("unknown tier", openai_send(text="not json", served="auto")),
                            ("missing tier", openai_send(text="not json", served=None)),
                            ("priority receipt", openai_send(text="not json", served="priority")),
                            ("fast over reservation", openai_send(text="not json", served="fast", usage=overrun)),
                            ("missing usage", openai_send(usage={"input_tokens": None})),
                            ("HTTP 429", openai_send(error=ReviewUnavailable("Provider HTTP 429"))),
                            ("timeout", openai_send(error=ReviewUnavailable("Provider transport/format error: Timeout")))):
            with self.subTest(label):
                error, _ = self.outcome(send)
                self.assertIsInstance(error, ReviewUnavailable, label)
                self.assertNotIsInstance(error, provider.InvalidModelOutput, label)

    def test_anthropic_incomplete_and_malformed_answers_are_invalid_model_output(self):
        import os
        from unittest.mock import patch
        for label, response in (("stopped at max tokens", {"stop_reason": "max_tokens", "content": []}),
                                ("non-string text", {"stop_reason": "end_turn", "content": [{"type": "text", "text": 5}]}),
                                ("invalid JSON", {"stop_reason": "end_turn", "content": [{"type": "text", "text": "x"}]})):
            with self.subTest(label):
                def send(url, headers, body, timeout=180, response=response):
                    if url.endswith("/count_tokens"):
                        return {"input_tokens": 400}
                    return {**response, "usage": {"input_tokens": 400, "output_tokens": 300}}
                with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-placeholder"}):
                    budget = Budget(cap=100_000)
                    with self.assertRaises(provider.InvalidModelOutput):
                        Model("claude-opus-5-5", transport=send).invoke("s", {}, budget, "unit", max_output=1500)
                self.assertTrue(budget.calls[0]["billing_known"])


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class DeadlineTests(unittest.TestCase):
    """One monotonic deadline; every timeout is clamped to it; nothing starts that cannot finish usefully."""

    def model(self, left, transport, advance_on_count=0.0):
        clock = Clock()

        def send(url, headers, body, timeout=180):
            if url.endswith("/input_tokens"):
                clock.now += advance_on_count
            return transport(url, headers, body, timeout)
        return Model("gpt-6.1-sol", transport=send, deadline=clock.now + left, clock=clock), clock

    def test_constants_fit_the_step_and_publication_headroom(self):
        self.assertEqual(provider.STEP_TIMEOUT_MINUTES, provider.JOB_TIMEOUT_MINUTES - 1)
        self.assertEqual(provider.REVIEW_DEADLINE_SECONDS,
                         provider.STEP_TIMEOUT_MINUTES * 60 - provider.PUBLICATION_HEADROOM_SECONDS)
        self.assertTrue(issubclass(provider.DeadlineReached, provider.BudgetExceeded))

    def test_without_a_deadline_the_fixed_timeouts_apply(self):
        transport = Transport(served="flex")
        Model("gpt-6.1-sol", transport=transport).invoke("s", {}, Budget(cap=30_000), "smoke", max_output=1500)
        self.assertEqual(transport.timeouts, [180])

    def test_timeouts_are_clamped_to_the_time_left(self):
        transport = Transport(served="flex")
        counts = []
        original = transport.__call__

        def record(url, headers, body, timeout=180):
            if url.endswith("/input_tokens"):
                counts.append(timeout)
            return original(url, headers, body, timeout)
        model, _ = self.model(130, record, advance_on_count=5)
        model.invoke("s", {}, Budget(cap=30_000), "smoke", max_output=1500)
        self.assertEqual(counts, [provider.COUNT_TIMEOUT_SECONDS])
        self.assertEqual(transport.timeouts, [125])            # 130 left, 5 spent counting, clamped below 180

    def test_no_request_starts_when_the_count_and_a_useful_response_cannot_fit(self):
        transport = Transport(served="flex")
        model, _ = self.model(provider.COUNT_TIMEOUT_SECONDS + provider.MIN_USEFUL_RESPONSE_SECONDS - 1, transport)
        budget = Budget(cap=30_000)
        with self.assertRaisesRegex(provider.DeadlineReached, "before a model request"):
            model.invoke("s", {}, budget, "smoke", max_output=1500)
        self.assertEqual((transport.generations, budget.calls, budget.spent), (0, [], 0))

    def test_no_generation_or_reservation_when_counting_used_up_the_time(self):
        transport = Transport(served="flex")
        model, _ = self.model(200, transport, advance_on_count=150)   # 50 s left after counting < 60 s
        budget = Budget(cap=30_000)
        with self.assertRaisesRegex(provider.DeadlineReached, "before a model response"):
            model.invoke("s", {}, budget, "smoke", max_output=1500)
        self.assertEqual((transport.generations, budget.calls, budget.spent), (0, [], 0))

    def test_the_timeout_is_recomputed_after_a_slow_durable_reservation(self):
        for advance, sent in ((provider.REVIEW_DEADLINE_SECONDS - 100, True), (provider.REVIEW_DEADLINE_SECONDS - 30, False)):
            with self.subTest(left_after_reservation=provider.REVIEW_DEADLINE_SECONDS - advance):
                transport = Transport(served="flex")
                clock = Clock()
                records = []

                def record(kind, rid, amount, advance=advance):
                    records.append((kind, rid, amount))
                    if kind == "R":
                        clock.now += advance    # posting the reservation status took this long
                budget = publish.DurableBudget(record, "9.1", spent=0)
                model = Model("gpt-6.1-sol", transport=transport, deadline=clock.now + provider.REVIEW_DEADLINE_SECONDS,
                              clock=clock)
                if sent:
                    model.invoke("s", {}, budget, "unit", max_output=1500)
                    self.assertEqual(transport.timeouts, [100])          # clamped after the reservation
                else:
                    with self.assertRaisesRegex(provider.DeadlineReached, "after reservation; request not sent"):
                        model.invoke("s", {}, budget, "unit", max_output=1500)
                    self.assertEqual(transport.generations, 0)
                    call = budget.calls[0]
                    self.assertEqual((call["sent"], call["billing_known"], call["actual_usd"]), (False, True, 0.0))
                    self.assertEqual(budget.spent, 0)
                    self.assertEqual([kind for kind, _, _ in records], ["R", "S"])   # a signed zero settlement
                    self.assertEqual(records[1][2], 0)

    def test_a_generation_timeout_keeps_its_reservation_as_unknown_billing_and_is_not_retried(self):
        transport = Transport(error=ReviewUnavailable("Provider transport/format error: TimeoutError"))
        model, _ = self.model(400, transport)
        budget = Budget(cap=30_000)
        with self.assertRaises(ReviewUnavailable):
            model.invoke("s", {}, budget, "smoke", max_output=1500)
        self.assertEqual(transport.generations, 1)
        self.assertFalse(budget.calls[0]["billing_known"])
        self.assertEqual(budget.spent, round(budget.calls[0]["reserved_usd"] * 1e6))


class PublisherWiringTests(unittest.TestCase):
    def test_the_publisher_uses_the_provider_default_model_and_sets_the_review_deadline(self):
        from test_publish import FakeGitHub, fake_review, publisher
        from literal_source import LiteralModel
        import inspect
        self.assertIs(inspect.signature(publish.Publisher).parameters["model_factory"].default, LiteralModel)
        github, made = FakeGitHub(), []

        def factory(name):
            made.append(LiteralModel(name, transport=Transport()))
            return made[-1]
        runner = publisher(github, fake_review({"status": "complete", "findings": []}))
        runner.model_factory = factory
        runner.model_name = "gpt-6.1-sol"
        runner.run()
        self.assertEqual(made[0].service_tier, "flex")
        self.assertAlmostEqual(made[0].deadline - runner.started, provider.REVIEW_DEADLINE_SECONDS)

    def test_diagnostics_report_only_known_tier_names(self):
        result = {"status": "complete", "findings": [], "budget": {"calls": [
            {"phase": "discovery", "model": "gpt-6.1-sol", "effort": "medium", "service_tier_requested": "flex",
             "service_tier_served": "flex"},
            {"phase": "verification", "model": "gpt-6.1-sol", "effort": "medium", "service_tier_requested": "flex",
             "service_tier_served": "auto <script>", "billing": "unknown_service_tier"}]}}
        calls = publish.review_diagnostics(result)["calls"]
        self.assertEqual([(c["service_tier_requested"], c["service_tier_served"]) for c in calls],
                         [("flex", "flex"), ("flex", None)])
        self.assertEqual([c["billing"] for c in calls], [None, "unknown_service_tier"])

    def test_workflow_limits_equal_the_constants(self):
        import re
        from pathlib import Path
        text = (Path(__file__).resolve().parents[1] / "workflows" / "jungle-review.yml").read_text()
        job = re.search(r"\n    timeout-minutes: (\d+)\n", text)
        step = re.search(r"- name: Review and publish\n        timeout-minutes: (\d+)\n", text)
        self.assertEqual(int(job.group(1)), provider.JOB_TIMEOUT_MINUTES)
        self.assertEqual(int(step.group(1)), provider.STEP_TIMEOUT_MINUTES)
        self.assertEqual(provider.RUNNER_ALLOWANCE_MICRODOLLARS, 6_000 * (9 + 6))
        self.assertEqual(provider.HARD_CAP_MICRODOLLARS, 410_000)


if __name__ == "__main__":
    unittest.main()
