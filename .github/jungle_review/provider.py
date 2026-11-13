"""Metered model calls. Reserve worst-case spend BEFORE sending any generation.

No provider retries are hidden inside this adapter. A request whose billing is
unknown consumes its full reservation and is marked unknown (billing_complete stays false).

Service tiers: an OpenAI model may request service_tier "flex" or "default" only; by default each model requests
DEFAULT_SERVICE_TIER (flex where its Flex price is verified, else default). Every call is still reserved at STANDARD rates. Settlement
follows the tier the response says served it: "flex" at FLEX_PRICING, "default" at PRICING,
"priority" or "fast" (Fast mode) at FAST_PRICING as known usage and then fail closed; any other or missing tier
is not settled (billing unknown) and fails closed. A standard-rate reservation does not by itself
cover a premium tier's charge: a premium receipt above the reservation is counted in full and stops.
Prices are USD per million tokens (including reasoning output), versioned here.
"""
from __future__ import annotations

import json
import hashlib
import math
import os
import secrets
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

# Cost ceiling per review scope (repository + PR + merge base + head), read conservatively as all-in:
# model API + GitHub Actions runner. The reviewer meters and caps model spend only. The runner part is a fixed
# allowance from GitHub's documented job timeout and 5-minute cancellation window (an expected bound, not a
# billing guarantee), and runs that end before the reviewer admits a review are not in it at all.
ALL_IN_CAP_MICRODOLLARS = 500_000
RUNNER_PRICE_MICRODOLLARS_PER_MINUTE = 6_000   # standard Linux x64 list price, fetched 2026-11-12
JOB_TIMEOUT_MINUTES = 9                        # equals jungle-review.yml's job timeout-minutes (tested)
CANCELLATION_ALLOWANCE_MINUTES = 6             # GitHub's documented 5-minute cancellation timeout + 1 rounding
RUNNER_ALLOWANCE_MICRODOLLARS = RUNNER_PRICE_MICRODOLLARS_PER_MINUTE * (JOB_TIMEOUT_MINUTES + CANCELLATION_ALLOWANCE_MINUTES)
HARD_CAP_MICRODOLLARS = ALL_IN_CAP_MICRODOLLARS - RUNNER_ALLOWANCE_MICRODOLLARS   # 410_000: the model cap
# Wall time. One monotonic review deadline per attempt, from publisher start, inside the "Review and publish"
# step's timeout and leaving room to publish: no request starts unless its token count and a useful response
# fit before it, and every transport timeout is clamped to what is left. No timeout is ever retried.
STEP_TIMEOUT_MINUTES = JOB_TIMEOUT_MINUTES - 1   # equals the workflow step's timeout-minutes (tested)
PUBLICATION_HEADROOM_SECONDS = 60
REVIEW_DEADLINE_SECONDS = STEP_TIMEOUT_MINUTES * 60 - PUBLICATION_HEADROOM_SECONDS   # 420
COUNT_TIMEOUT_SECONDS = 45
MIN_USEFUL_RESPONSE_SECONDS = 60
PRICING = {
    "claude-opus-5-5": {"input": 4, "cached": 0.2, "output": 20},
    "gpt-6.1-sol": {"input": 2, "cached": 0.1, "output": 10},
}
# Standard global rates verified 2026-11-12 against:
# https://platform.claude.com/docs/en/about-claude/pricing
# https://developers.openai.com/api/docs/models/gpt-6.1-sol
# Never cross a tier where this price table would understate the charge.
# Flex and Fast (priority) short-context rates, OpenAI pricing page fetched 2026-11-12
# (.context/reviewer-research/design/pricing-current.md, "Flex" and "Fast pricing data").
FLEX_PRICING = {"gpt-6.1-sol": {"input": 1, "cached": 0.05, "output": 5}}
FAST_PRICING = {"gpt-6.1-sol": {"input": 4, "cached": 0.2, "output": 20}}
REQUESTABLE_SERVICE_TIERS = ("default", "flex")
DEFAULT_SERVICE_TIER = {"gpt-6.1-sol": "flex"}   # production default per model; everything else requests "default"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 180
MAX_STANDARD_INPUT_TOKENS = {"claude-opus-5-5": 200000, "gpt-6.1-sol": 272000}


class ReviewUnavailable(RuntimeError):
    pass


class ResourceUnavailable(ReviewUnavailable):
    """Explicit Flex 429 resource-unavailable rejection, documented as uncharged."""


class BudgetExceeded(ReviewUnavailable):
    pass


class InvalidModelOutput(ReviewUnavailable):
    """The model answered and its usage was settled at a known, expected tier (default or flex), but the output
    is unusable: invalid JSON, a non-object result, a malformed output shape, or an incomplete response. The
    charge is recorded and billing is complete for that call, so a caller may mark just that unit incomplete
    and continue. Never raised for an unknown or premium tier, a usage or pricing contract problem, or a
    transport error: those stay ReviewUnavailable and must stop. Nothing is ever retried."""


class DeadlineReached(BudgetExceeded):
    """Not enough wall time left before the review deadline: nothing was reserved or sent."""


@dataclass
class Budget:
    cap: int = HARD_CAP_MICRODOLLARS
    spent: int = 0
    calls: list[dict] = field(default_factory=list)
    # The highest cap a budget of this class may carry. A plain Budget is a model budget (at most the model
    # cap); the publisher's durable scope ledger is all-in (model + reserved runner allowances).
    MAX_CAP_MICRODOLLARS = HARD_CAP_MICRODOLLARS

    def __post_init__(self):
        ceiling = type(self).MAX_CAP_MICRODOLLARS
        if type(self.cap) is not int or not 0 < self.cap <= ceiling:
            raise ValueError(f"Review budget must be between $0 and ${ceiling / 1e6:.2f}")

    @property
    def remaining(self):
        return self.cap - self.spent

    def reserve(self, amount: int, metadata: dict) -> dict:
        if amount > self.remaining or amount < 0:
            raise BudgetExceeded("Insufficient remaining budget; no model request sent")
        self.spent += amount
        call = {**metadata, "reserved_usd": amount / 1e6,
                "charged_upper_bound_usd": amount / 1e6, "billing_known": False}
        self.calls.append(call)
        return call

    def settle(self, call: dict, actual: int, usage: dict):
        reserved = round(call["charged_upper_bound_usd"] * 1e6)
        if actual > reserved:
            # Provider pricing/counting contract changed. Fail closed, never
            # start another call. Preserve the actual evidence for operators.
            self.spent += actual - reserved
            call.update(actual_usd=actual / 1e6, usage=usage)
            raise ReviewUnavailable("Provider usage exceeded reservation; inspect pricing contract")
        self.spent -= reserved - actual
        call.update(charged_upper_bound_usd=actual / 1e6,
                    actual_usd=actual / 1e6, billing_known=True, usage=usage)

    def report(self):
        """cap_usd and cost_upper_bound_usd are always MODEL figures. A plain Budget holds model spend only."""
        return {"cap_usd": self.cap / 1e6, "cost_upper_bound_usd": self.spent / 1e6,
                "model_cap_usd": self.cap / 1e6, "model_cost_upper_bound_usd": self.spent / 1e6,
                "runner_allowance_usd": 0.0,
                "billing_complete": all(c["billing_known"] for c in self.calls),
                "calls": self.calls}


def post_json(url: str, headers: dict, body: dict, timeout=180) -> dict:
    request = urllib.request.Request(url, data=json.dumps(body).encode(),
                                    headers={**headers, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        # Do not echo response bodies: they can contain PR text or credentials.
        if error.code == 429:
            try:
                detail = json.loads(error.read(65536)).get("error", {})
                if detail.get("code") == "resource_unavailable" or detail.get("type") == "resource_unavailable":
                    raise ResourceUnavailable("Flex resources unavailable; request rejected without charge") from None
            except (ValueError, TypeError, AttributeError):
                pass
        raise ReviewUnavailable(f"Provider HTTP {error.code}") from None
    except (OSError, ValueError) as error:
        raise ReviewUnavailable(f"Provider transport/format error: {type(error).__name__}") from None


def openai_output_text(output) -> str:
    """The concatenated output_text of a Responses API output list. Any other shape (a non-list output, a
    non-object item or part, non-list content, non-string text) is ReviewUnavailable with no response text;
    callers settle usage before calling this, so a malformed paid response keeps its ledger evidence."""
    if not isinstance(output, list):
        raise InvalidModelOutput("Malformed provider output; usage settled")
    pieces = []
    for item in output:
        if not isinstance(item, dict):
            raise InvalidModelOutput("Malformed provider output; usage settled")
        if item.get("type") != "message":
            continue
        content = item.get("content", [])
        if not isinstance(content, list):
            raise InvalidModelOutput("Malformed provider output; usage settled")
        for part in content:
            if not isinstance(part, dict):
                raise InvalidModelOutput("Malformed provider output; usage settled")
            if part.get("type") == "output_text":
                text = part.get("text", "")
                if not isinstance(text, str):
                    raise InvalidModelOutput("Malformed provider output; usage settled")
                pieces.append(text)
    return "".join(pieces)


def parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3].strip()
    elif text.startswith("```\n") and text.endswith("```"):
        text = text[4:-3].strip()
    try:
        result = json.loads(text)
    except ValueError:
        raise InvalidModelOutput("Reviewer returned invalid JSON") from None
    if not isinstance(result, dict):
        raise InvalidModelOutput("Reviewer returned a non-object result")
    return result


class Model:
    def __init__(self, model="gpt-6.1-sol", transport: Callable = post_json, service_tier: str | None = None,
                 request_timeout: int = DEFAULT_REQUEST_TIMEOUT_SECONDS, deadline: float | None = None,
                 clock: Callable[[], float] = time.monotonic, cache_prefix: bool = False):
        if model not in PRICING:
            raise ValueError("Unsupported model: pricing must be explicitly verified")
        if service_tier is None:
            service_tier = DEFAULT_SERVICE_TIER.get(model, "default")
        if service_tier not in REQUESTABLE_SERVICE_TIERS:
            raise ValueError("Unsupported service tier: only default and flex may be requested")
        if service_tier == "flex" and model not in FLEX_PRICING:
            raise ValueError("Flex is not verified for this model")
        if type(request_timeout) is not int or not 0 < request_timeout <= DEFAULT_REQUEST_TIMEOUT_SECONDS:
            raise ValueError("Request timeout must be 1-180 seconds")
        self.cache_prefix = bool(cache_prefix)
        self.cache_namespace = secrets.token_hex(16)
        self.model = model
        self.prices = PRICING[model]   # reservations always use standard rates
        self.transport = transport
        self.anthropic = model.startswith("claude-")
        if self.anthropic and service_tier != "default":
            raise ValueError("Service tiers are an OpenAI option here")
        self.service_tier = service_tier
        self.request_timeout = request_timeout
        self.deadline = deadline   # a clock() value; None means no deadline (offline tools and tests)
        self.clock = clock

    def time_left(self):
        return None if self.deadline is None else self.deadline - self.clock()

    def timeout_within_deadline(self, limit: float, needed: float, what: str) -> float:
        """limit, clamped to the time left; DeadlineReached (nothing sent) when less than `needed` is left."""
        left = self.time_left()
        if left is None:
            return limit
        if left < needed:
            raise DeadlineReached(f"Review deadline reached before {what}; no request sent")
        return min(limit, left)

    def headers(self):
        name = "ANTHROPIC_API_KEY" if self.anthropic else "OPENAI_API_KEY"
        key = os.environ.get(name)
        if not key:
            raise ReviewUnavailable(f"Missing {name}")
        if self.anthropic:
            return {"x-api-key": key, "anthropic-version": "2023-06-01"}
        return {"Authorization": f"Bearer {key}"}

    def prepare(self, system, payload, phase, cache_context=None, instruction=None):
        control = {"phase": phase}
        if instruction is not None:
            control["instruction"] = instruction
        prompt = json.dumps({"control": control, "response_format": "JSON object", "evidence": payload},
                            ensure_ascii=False, separators=(",", ":"))
        if self.anthropic:
            content = prompt
            if cache_context is not None:
                content = [{"type": "text", "text": json.dumps(cache_context, ensure_ascii=False,
                            separators=(",", ":")), "cache_control": {"type": "ephemeral", "ttl": "5m"}},
                           {"type": "text", "text": prompt}]
            return {"model": self.model, "system": system,
                    "messages": [{"role": "user", "content": content}]}
        model_input = prompt
        if cache_context is not None:
            prefix = json.dumps({"review_namespace": self.cache_namespace, "source": cache_context},
                                ensure_ascii=False, separators=(",", ":"))
            content = ([{"type": "input_text", "text": prefix,
                         "prompt_cache_breakpoint": {"mode": "explicit", "ttl": "30m"}}]
                       if self.cache_prefix else prefix)
            model_input = [{"role": "user", "content": content}, {"role": "user", "content": prompt}]
        return {"model": self.model, "instructions": system, "input": model_input}

    def input_cost(self, body, headers):
        url = ("https://api.anthropic.com/v1/messages/count_tokens" if self.anthropic else
               "https://api.openai.com/v1/responses/input_tokens")
        timeout = self.timeout_within_deadline(COUNT_TIMEOUT_SECONDS, 1, "token counting")
        counted = self.transport(url, headers, body, timeout=timeout)
        input_tokens = counted.get("input_tokens") if isinstance(counted, dict) else None
        if type(input_tokens) is not int or input_tokens < 0:
            raise ReviewUnavailable("Input-token preflight unavailable; no generation sent")
        if input_tokens > MAX_STANDARD_INPUT_TOKENS[self.model]:
            raise BudgetExceeded("Input exceeds the verified standard pricing tier; no generation sent")
        reserved_input = math.ceil(input_tokens * 1.10) + 1024
        return input_tokens, math.ceil(reserved_input * self.prices["input"] * 1.25)

    def required_budget(self, system, payload, phase, *, cache_context=None, instruction=None, output_tokens=4000):
        body = self.prepare(system, payload, phase, cache_context, instruction)
        _, input_ceiling = self.input_cost(body, self.headers())
        return input_ceiling + output_tokens * self.prices["output"]

    def invoke(self, system: str, payload: dict, budget: Budget, phase: str,
               max_output=7000, ceiling=None, cache_context=None, instruction=None, effort="medium",
               minimum_output=1200) -> dict:
        """Exactly one generation, including adaptive/medium reasoning in cap."""
        if effort not in ("medium", "high"):
            raise ValueError("Unsupported reasoning effort")
        headers = self.headers()
        # A request starts only if its token count and a useful response both fit before the deadline.
        self.timeout_within_deadline(0, COUNT_TIMEOUT_SECONDS + MIN_USEFUL_RESPONSE_SECONDS, "a model request")
        count_body = self.prepare(system, payload, phase, cache_context, instruction)
        input_tokens, input_ceiling = self.input_cost(count_body, headers)
        # Checked before reserving (so a hopeless call reserves nothing) and again right before sending.
        self.timeout_within_deadline(self.request_timeout, MIN_USEFUL_RESPONSE_SECONDS, "a model response")
        # Count endpoints are estimates and may omit provider-injected reasoning
        # scaffolding. Reserve uncached input plus 10% and 1024 tokens. Caching
        # can reduce cost; no paid tools are enabled.
        # Reserve the cache-write rate too: automatic prompt caching must not
        # turn a request budgeted at the base rate into an overrun.
        available = min(budget.remaining, ceiling if ceiling is not None else budget.remaining)
        max_output = min(max_output, math.floor(
            (available - input_ceiling) / self.prices["output"]))
        if max_output < minimum_output:
            raise BudgetExceeded("Insufficient budget for a useful response; no generation sent")
        reservation = input_ceiling + max_output * self.prices["output"]
        call = budget.reserve(reservation, {"model": self.model, "effort": effort, "phase": phase,
                                           "input_tokens_preflight": input_tokens,
                                           "max_output_tokens": max_output})
        # Reserving can be slow (the publisher's ledger posts a GitHub status synchronously), so the
        # generation timeout is computed only now, immediately before the send. If no useful time is left the
        # request is not sent: nothing can be charged, so the reservation is settled to a known zero.
        left = self.time_left()
        if left is not None and left < MIN_USEFUL_RESPONSE_SECONDS:
            call["sent"] = False
            budget.settle(call, 0, {"not_sent": "review deadline reached after reservation"})
            raise DeadlineReached("Review deadline reached after reservation; request not sent")
        generation_timeout = self.request_timeout if left is None else min(self.request_timeout, left)
        started = time.monotonic()
        try:
            if self.anthropic:
                body = {**count_body, "max_tokens": max_output,
                        "thinking": {"type": "adaptive"}, "output_config": {"effort": effort}}
                response = self.transport("https://api.anthropic.com/v1/messages", headers, body,
                                          timeout=generation_timeout)
                if not isinstance(response, dict) or not isinstance(response.get("usage"), dict):
                    raise ReviewUnavailable("Missing provider usage; full reservation retained")
                usage = response["usage"]
                n_input, n_output = usage.get("input_tokens"), usage.get("output_tokens")
                if not all(type(n) is int and n >= 0 for n in (n_input, n_output)):
                    raise ReviewUnavailable("Missing provider usage; full reservation retained")
                cache_read = usage.get("cache_read_input_tokens", 0)
                cache_write = usage.get("cache_creation_input_tokens", 0)
                if any(type(n) is not int or n < 0 for n in (cache_read, cache_write)):
                    raise ReviewUnavailable("Invalid provider cache usage; full reservation retained")
                long_writes = (usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens", 0)
                if long_writes:
                    raise ReviewUnavailable("Unexpected long-cache billing; full reservation retained")
                actual = math.ceil(n_input * self.prices["input"] + n_output * self.prices["output"]
                                   + cache_read * self.prices["cached"]
                                   + cache_write * self.prices["input"] * 1.25)
                budget.settle(call, actual, usage)
                if response.get("stop_reason") != "end_turn":
                    raise InvalidModelOutput("Reviewer did not finish within its reserved output budget")
                content = response.get("content", [])
                if not isinstance(content, list) or not all(isinstance(x, dict) for x in content):
                    raise InvalidModelOutput("Malformed provider output; usage settled")
                pieces = [x.get("text", "") for x in content if x.get("type") == "text"]
                if not all(isinstance(piece, str) for piece in pieces):
                    raise InvalidModelOutput("Malformed provider output; usage settled")
                text = "".join(pieces)
            else:
                body = {**count_body, "max_output_tokens": max_output,
                        "reasoning": {"effort": effort}, "store": False, "service_tier": self.service_tier,
                        "text": {"format": {"type": "json_object"}},
                        "prompt_cache_options": {"mode": "explicit", "ttl": "30m"}}
                if cache_context is not None:
                    body["prompt_cache_key"] = hashlib.sha256((self.model + system + json.dumps(
                        cache_context, ensure_ascii=False, separators=(",", ":"))).encode()).hexdigest()
                call["service_tier_requested"] = self.service_tier
                response = self.transport("https://api.openai.com/v1/responses", headers, body,
                                          timeout=generation_timeout)
                if not isinstance(response, dict) or not isinstance(response.get("usage"), dict):
                    raise ReviewUnavailable("Missing provider usage; full reservation retained")
                usage = response["usage"]
                n_input, n_output = usage.get("input_tokens"), usage.get("output_tokens")
                if not all(type(n) is int and n >= 0 for n in (n_input, n_output)):
                    raise ReviewUnavailable("Missing provider usage; full reservation retained")
                cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
                writes = (usage.get("input_tokens_details") or {}).get("cache_write_tokens", 0)
                if any(type(n) is not int or n < 0 for n in (cached, writes)) or cached + writes > n_input:
                    raise ReviewUnavailable("Invalid provider cache usage; full reservation retained")
                served = response.get("service_tier")
                call["service_tier_served"] = served if isinstance(served, str) else None
                rates = {"flex": FLEX_PRICING.get(self.model), "default": PRICING[self.model],
                         "priority": FAST_PRICING.get(self.model),
                         "fast": FAST_PRICING.get(self.model)}.get(served) if isinstance(served, str) else None
                if rates is None:
                    # Unknown or missing tier: its price is unknown, so nothing is settled. The standard-rate
                    # reservation stays counted, billing stays unknown, and no further call is made. That
                    # reservation is not proof the unknown tier's charge fits in it.
                    call["billing"] = "unknown_service_tier"
                    raise ReviewUnavailable("Unknown provider service tier; billing unknown, full reservation retained")
                details = (usage.get("input_tokens_details") or {}).get("cache_write_tokens_details") or {}
                prefix_requested = self.cache_prefix and cache_context is not None
                proven_short = (type(details.get("ttl_30m_tokens")) is int
                                and details["ttl_30m_tokens"] == writes and details.get("ttl_12h_tokens") == 0)
                cache_contract_ok = ((prefix_requested and (not writes or proven_short))
                                     or (not prefix_requested and not writes and not cached))
                actual = math.ceil((n_input - cached - writes) * rates["input"]
                                   + cached * rates["cached"] + writes * rates["input"] * (1.25 if proven_short else 2.5)
                                   + n_output * rates["output"])
                budget.settle(call, actual, usage)  # an actual above the reservation is counted and raises
                if not cache_contract_ok:
                    raise ReviewUnavailable("Explicit cache contract violated; usage recorded conservatively")
                if served in ("priority", "fast"):
                    # Known premium usage was recorded above; a tier this review did not request stops it.
                    raise ReviewUnavailable("Provider served a premium tier that was not requested; stopped")
                if response.get("status") != "completed":
                    raise InvalidModelOutput("Reviewer did not finish within its reserved output budget")
                text = openai_output_text(response.get("output", []))
            return parse_json(text)
        except ResourceUnavailable:
            if not self.anthropic and self.service_tier == "flex":
                call["rejection"] = "resource_unavailable"
                budget.settle(call, 0, {"rejected_http_status": 429, "error_code": "resource_unavailable"})
            raise
        except InvalidModelOutput:
            call["output"] = "invalid"   # settled and billed; the output itself is unusable
            raise
        finally:
            call["elapsed_seconds"] = round(time.monotonic() - started, 3)
