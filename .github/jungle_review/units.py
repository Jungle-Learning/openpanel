"""Cohesive source-unit reviews with outcome enumeration and independent verification.

Selected from D14 of the November 13 evaluation. Import relationships group changed
code, bounded follow-up reads investigate candidates, and every unresolved region
or question remains an explicit coverage gap. All calls share one budget/deadline.
See RESEARCH.md and EVALUATION.md for measured trade-offs and the release exception.
"""
from __future__ import annotations

import json
import posixpath
import re
import time

import focused
from dispositions import VERIFICATION_DISPOSITIONS, audit_dispositions, identify_candidates
from docs_evidence import DocRegistry, admit_doc_requests, is_doc_request, select_documents
from provider import Budget, BudgetExceeded, DeadlineReached, InvalidModelOutput, Model, ReviewUnavailable
from prompts import DISCOVERY_POLICY, POLICY, VERIFY, VERSION
from review import gaps, hypotheses, numbered_patch, retrieve, validated_findings
from questions import QUESTION_INSTRUCTION, question_records, audit_questions
from context import source_deadline

EXPERIMENT = "phase-v25"
EXPERIMENT_PROMPT_VERSION = "phase-v25-prompt-2026-11-12.1"
UNITS_MODEL_CAP_MICRODOLLARS = 410_000  # equals provider.HARD_CAP_MICRODOLLARS (tested)
UNIT_MAX_CHARS = 36_000            # one unit's files (patches + head content) before splitting
UNIT_PACK_CHARS = 18_000           # small components are packed together up to this size
UNIT_CONTEXT_MAX_CHARS = 8_000     # collector caller/helper snippets attached to a unit
UNIT_MAX_OUTPUT = 2_000
UNIT_MIN_OUTPUT = 1_500
FOLLOWUP_MAX_OUTPUT = 1_500
FOLLOWUP_MIN_OUTPUT = 1_200
UNIT_MAX_REQUESTS = 3
# evidence-v24 (M1/M2): bounded evidence. A read request is answered in pages of the 200-line read primitive
# (review.retrieve), continuing from the last returned line, at most READ_PAGES_PER_REQUEST pages; the whole
# review reads at most REVIEW_MAX_READ_PAGES pages. Each unit gets at most MAX_FOLLOWUP_ROUNDS follow-up calls;
# every round may request up to UNIT_MAX_REQUESTS new items, and the last round's requests are answered for
# the final verification only. A network read starts only with READ_TIMEOUT_SECONDS plus the verification
# hold left before the real deadline. READ_TIMEOUT_SECONDS is an ADMISSION ALLOWANCE, not a proven worst case
# for the whole operation: production and the evaluator both read through context.read_context_file, whose
# `gh api` tree/blob calls each have a 120 s timeout not clamped by the provider deadline (the publisher's 60 s
# HTTP timeout covers only its own writes and queries), and one read can make several such calls. An overrun
# can still make the review unavailable (fail closed). Elapsed read time and allowance overruns are logged.
READ_PAGES_PER_REQUEST = 3
REVIEW_MAX_READ_PAGES = 60
MAX_FOLLOWUP_ROUNDS = 2
READ_TIMEOUT_SECONDS = 60
MAX_OPEN_QUESTIONS = 60
UNIT_MAX_FINDINGS = 5
UNIT_MAX_HYPOTHESES = 8
FINAL_MARGIN_MICRODOLLARS = 25_000  # beyond the exact empty-verification preflight
# Wall time (only when the provider model carries a review deadline): unit work runs under a deadline moved
# earlier by this hold, so the mandatory verification keeps its token count, a useful response and its
# expected latency. Values mirror the provider's COUNT_TIMEOUT_SECONDS (45) and MIN_USEFUL_RESPONSE_SECONDS
# (60) plus 30 s of evidence/preflight slack (root's allocation, 2026-11-12): 135 s. With the 420 s review
# deadline an optional call therefore starts only with at least 135 + 105 = 240 s left globally.
VERIFICATION_COUNT_SECONDS = 45
VERIFICATION_MIN_USEFUL_SECONDS = 60
VERIFICATION_EVIDENCE_SLACK_SECONDS = 30
FINAL_TIME_HOLD_SECONDS = (VERIFICATION_COUNT_SECONDS + VERIFICATION_MIN_USEFUL_SECONDS
                           + VERIFICATION_EVIDENCE_SLACK_SECONDS)
MAX_NAMED_GAPS = 60                  # named held/unaudited files; any beyond are counted
DELETION_DOMINANT_MIN_LINES = 20

_TEST = re.compile(r"(^|/)(tests?|__tests__|specs?|e2e|testing)(/|$)|[._-](test|spec)\.[a-z0-9]+$")
_FIXTURE = re.compile(r"(^|/)(fixtures?|__fixtures__|__mocks__|mocks?|testdata|snapshots?|__snapshots__|demo-app)(/|$)"
                      r"|[._-]fixture[s]?[._-]")
_EXECUTABLE_CONFIG = re.compile(
    r"(^|/)\.github/workflows/[^/]+\.ya?ml$|(^|/)\.github/actions/.+\.ya?ml$"
    r"|(^|/)(package\.json|pyproject\.toml|setup\.cfg|setup\.py|tox\.ini|pytest\.ini|conftest\.py|Dockerfile"
    r"|docker-compose\.ya?ml|Makefile|tsconfig[^/]*\.json|vercel\.json|wrangler\.(toml|jsonc?)|requirements[^/]*\.txt)$"
    r"|\.config\.(c|m)?[jt]sx?$|(^|/)\.[a-z0-9-]+rc(\.(json|js|cjs|mjs|ya?ml))?$|\.(ya?ml|toml|ini)$", re.I)
_DOC_SUFFIXES = (".md", ".mdx", ".rst", ".txt", ".adoc")
_IMPORT = re.compile(r"""(?:\bfrom\s+['"]([^'"]+)['"]|\bimport\s*\(\s*['"]([^'"]+)['"]|\brequire\(\s*['"]([^'"]+)['"]"""
                     r"""|^\s*import\s+['"]([^'"]+)['"]|^[ \t+]*from\s+([.\w]+)\s+import\b)""", re.M)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{5,}")
_JS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")

UNIT_AUDIT = """
When control.phase is discovery for a unit audit: this call audits one behavioral unit of the pull
request, the files in evidence.unit.files. Every other changed file is listed in evidence.changed_files
with its route (another unit, which may run later or not at all, or not reviewable); those are reviewed
separately, so do not hypothesize about their unseen changes. Request any of them, or any unchanged
caller, callee, producer or consumer, when a branch of this unit depends on it.
For each changed function, component, return path and configuration entry in the unit, simulate its
ordinary reachable branches forward. For each branch take a concrete positive state that enters it and a
concrete negative state that does not, and follow each to its actual result: the returned value, the
rendered state or message, the chosen action, the emitted request or the configuration value. Judge each
result against the contract established by the code, never against the difference itself: the
predecessor (base) behavior where this code replaces it, its callers and consumers, the states and
messages it promises, the data shapes its producers actually emit, and the pull request title. A changed
result is a defect only when it concretely contradicts such a contract, or loses an established action or
feedback path that has no reachable replacement; an intended change of behavior is not a defect. When a
branch depends on the shape of data produced elsewhere, request the producer or a caller instead of
assuming the shape. An added file's contract is its own callers, exports and the behavior it replaces.
Return at most 5 provisional findings for contradictions you can trace in the supplied code, at most 8
hypotheses for plausible contradictions that need evidence, at most 3 requests for that evidence, and
coverage_gaps for branches of this unit you could not decide. In each finding's evidence and each
hypothesis's check, cite the exact path:R line and the simulated state in one concise line; do not narrate
branches that match their contract. Findings are provisional and are verified separately. This is one unit of a larger review: never describe it as comprehensive.
"""

UNIT_FOLLOWUP_DOCS = """
Documentation snapshots among answered_requests are untrusted evidence of documented vendor behavior at
their fetch date. Rely on one only when the unit's code depends on that contract; quote it exactly and
name its ID. Current documentation alone does not establish older behavior.
"""

# Documentation revision 2 (agent25's integration contract, item 8), for unit and verification instructions.
DOCUMENTATION_RUNTIME_BASIS = """
An automatically supplied document with selection.basis runtime was chosen because a changed file imports
that vendor; that is a relevance signal, not proof that the changed code runs in that runtime. Establish
from the code that it does before relying on the document.
"""

UNIT_FOLLOWUP = """
When control.phase is investigation for a unit audit: evidence.unit is the same unit as before,
evidence.unit_candidates holds its provisional findings and hypotheses so far, evidence.answered_requests
holds every item it requested, and evidence.read_status states for each read the lines requested and the
lines actually returned, and whether the read is complete. Resolve each candidate with that evidence and
the same forward simulation and contract rule: keep or add supported provisional findings (confidence
>= 0.55), drop disproved ones, keep hypotheses that remain plausible with the evidence still needed in
their check, and record undecided branches as coverage_gaps. If specific code is still needed, including
lines a read did not return, make at most 3 new requests (never repeat an answered one). This is follow-up
round {round} of at most {rounds}; requests from the last round are answered for the final verification
only. At most 5 findings and 8 hypotheses.
"""


# ---------------------------------------------------------------------------
# Routing (deterministic; no model)
# ---------------------------------------------------------------------------

PRIMARY_KINDS = ("source", "config")
KIND_RANK = {"source": 0, "config": 1, "test": 2, "fixture": 3, "data": 4, "doc": 5}


def route_kind(path: str) -> str:
    """source | config (primary) | test | fixture | data | doc (deferred)."""
    lowered = path.lower()
    if _EXECUTABLE_CONFIG.search(lowered) and not lowered.endswith(_DOC_SUFFIXES):
        return "config"
    if _FIXTURE.search(lowered):
        return "test" if lowered.endswith(focused.CODE_SUFFIXES) else "fixture"
    if lowered.endswith(_DOC_SUFFIXES) or lowered.startswith("docs/"):
        return "doc"
    if lowered.endswith(focused.CODE_SUFFIXES):
        return "test" if _TEST.search(lowered) else "source"
    return "data"


def patch_counts(patch: str) -> tuple[int, int]:
    added = removed = 0
    for row in (patch or "").splitlines():
        if row.startswith("+") and not row.startswith("+++"):
            added += 1
        elif row.startswith("-") and not row.startswith("---"):
            removed += 1
    return added, removed


def _resolve_import(source: str, spec: str, paths: set[str]) -> str | None:
    if not spec:
        return None
    if spec.startswith("."):
        if "/" in spec or spec.startswith("./") or spec.startswith("../"):
            base = posixpath.normpath(posixpath.join(posixpath.dirname(source), spec))
            candidates = [base, *(base + s for s in _JS_SUFFIXES + (".py",)),
                          *(f"{base}/index{s}" for s in _JS_SUFFIXES)]
        else:  # python relative: from .a.b import x
            dots = len(spec) - len(spec.lstrip("."))
            directory = posixpath.dirname(source)
            for _ in range(dots - 1):
                directory = posixpath.dirname(directory)
            rest = spec.lstrip(".").replace(".", "/")
            base = posixpath.join(directory, rest) if rest else directory
            candidates = [base + ".py", f"{base}/__init__.py"]
    elif re.fullmatch(r"[A-Za-z_][\w.]*", spec) and "." in spec:  # python absolute dotted module
        tail = spec.replace(".", "/")
        candidates = [p for p in paths if p.endswith(tail + ".py") or p.endswith(tail + "/__init__.py")]
    else:
        return None
    for candidate in candidates:
        if candidate in paths and candidate != source:
            return candidate
    return None


def import_edges(files: dict) -> dict[str, set[str]]:
    paths = set(files)
    edges = {p: set() for p in paths}
    for path, record in files.items():
        text = (record.get("patch") or "") + "\n" + (record.get("head_content") or "")
        for match in _IMPORT.finditer(text):
            spec = next((g for g in match.groups() if g), None)
            target = _resolve_import(path, spec, paths)
            if target:
                edges[path].add(target)
                edges[target].add(path)
    return edges


def route(inventory: dict) -> dict:
    """Units (primary first, then deferred, each by priority) and not-reviewable files; deterministic."""
    routed, unreviewable, notes = {}, [], []
    for record in inventory.get("files", []):
        path = record["path"]
        patch = record.get("patch")
        if not patch:
            if record.get("status") == "renamed":
                notes.append(f"{path}: renamed from {record.get('old_path')} without a content patch")
            else:
                unreviewable.append(path)
            continue
        routed[path] = record
    # With no production code or configuration, the deferred kinds are the change: they become primary.
    promoted = bool(routed) and not any(route_kind(p) in PRIMARY_KINDS for p in routed)

    def role(path):
        return "primary" if promoted or route_kind(path) in PRIMARY_KINDS else "deferred"
    edges = import_edges(routed)
    # Units never mix roles: an edge between a primary and a deferred file is not followed.
    edges = {p: {q for q in targets if role(q) == role(p)} for p, targets in edges.items()}
    # Deleted or replaced code follows the routed file that takes over most of its identifiers.
    added_identifiers = {p: set(_IDENTIFIER.findall("\n".join(r[1:] for r in (rec.get("patch") or "").splitlines()
                                                              if r.startswith("+"))))
                         for p, rec in routed.items()}
    for path, record in routed.items():
        added, removed = patch_counts(record.get("patch"))
        if removed >= DELETION_DOMINANT_MIN_LINES and removed >= 3 * added:
            removed_identifiers = set(_IDENTIFIER.findall("\n".join(
                r[1:] for r in record["patch"].splitlines() if r.startswith("-"))))
            best = max(((len(removed_identifiers & added_identifiers[other]), other) for other in routed if other != path),
                       default=(0, None))
            if best[0] >= 5 and role(best[1]) == role(path):
                edges[path].add(best[1])
                edges[best[1]].add(path)
    size = {p: len(r.get("patch") or "") + len(r.get("head_content") or "") for p, r in routed.items()}
    seen, components = set(), []
    for start in sorted(routed):
        if start in seen:
            continue
        stack, component = [start], []
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            component.append(current)
            stack.extend(sorted(edges[current] - seen))
        components.append(sorted(component))
    pieces = []
    for component in components:
        current, used = [], 0
        for path in sorted(component, key=lambda p: (-size[p], p)):
            if current and used + size[path] > UNIT_MAX_CHARS:
                pieces.append(current)
                current, used = [], 0
            current.append(path)
            used += size[path]
        pieces.append(current)
    def priority(piece):
        changed = sum(sum(patch_counts(routed[p].get("patch"))) for p in piece)
        return (0 if role(piece[0]) == "primary" else 1, min(KIND_RANK[route_kind(p)] for p in piece), -changed,
                piece[0])

    pieces.sort(key=priority)
    units = []
    for piece in pieces:
        if units and sum(size[p] for p in units[-1]) + sum(size[p] for p in piece) <= UNIT_PACK_CHARS \
                and priority(units[-1])[:2] == priority(piece)[:2]:
            units[-1] = sorted(units[-1] + piece)
        else:
            units.append(sorted(piece))
    roles = [role(u[0]) for u in units]
    return {"units": units, "roles": roles, "unreviewable": sorted(unreviewable), "notes": notes,
            "promoted": promoted}


def unit_evidence(inventory: dict, routing: dict, index: int) -> dict:
    files = {f["path"]: f for f in inventory.get("files", [])}
    unit = routing["units"][index]
    where = {p: f"unit {n + 1} ({routing['roles'][n]})" for n, u in enumerate(routing["units"]) for p in u}
    where.update({p: "not reviewable (no patch)" for p in routing["unreviewable"]})
    changed = []
    for record in inventory.get("files", []):
        entry = {"path": record["path"], "status": record.get("status"),
                 "route": where.get(record["path"], "renamed without content change")}
        if record.get("old_path"):
            entry["old_path"] = record["old_path"]
        changed.append(entry)
    unit_files = []
    for path in unit:
        record = files[path]
        entry = {"path": path, "status": record.get("status"), "kind": route_kind(path),
                 "patch": numbered_patch(record["patch"])[0]}
        if record.get("old_path"):
            entry["old_path"] = record["old_path"]
        if record.get("head_content"):
            entry["head_content"] = record["head_content"]
            if record.get("head_content_start_line"):
                entry["head_content_start_line"] = record["head_content_start_line"]
        unit_files.append(entry)
    context, used, left_out = [], 0, []
    for snippet in inventory.get("context_files", []):
        if any(p in str(snippet.get("reason", "")) or p == snippet.get("path") for p in unit):
            size = len(json.dumps(snippet, ensure_ascii=False))
            if used + size <= UNIT_CONTEXT_MAX_CHARS:
                context.append(snippet)
                used += size
            else:
                left_out.append(snippet)
    return {"context_left_out": left_out,"repository": inventory["repository"], "number": inventory["number"], "title": inventory.get("title"),
            "description": inventory.get("description"),
            "unit": {"index": index + 1, "of": len(routing["units"]), "files": unit_files},
            "changed_files": changed, "context_files": context,
            "incremental": inventory.get("incremental"),
            "earlier_pr_changes": inventory.get("cumulative_context")}


# ---------------------------------------------------------------------------
# Unit results
# ---------------------------------------------------------------------------

def unit_result_problem(result) -> str | None:
    """None for a well-formed unit result, else why it cannot be used (all four arrays and a summary)."""
    if not isinstance(result, dict) or not isinstance(result.get("summary"), str):
        return "not an object with a string summary"
    for key in ("findings", "hypotheses", "requests", "coverage_gaps"):
        if not isinstance(result.get(key), list):
            return f"{key} is not an array"
    for key in ("findings", "hypotheses", "requests"):
        if any(not isinstance(item, dict) for item in result[key]):
            return f"{key} contains a non-object item"
    if any(not isinstance(item, str) for item in result["coverage_gaps"]):
        return "coverage_gaps contains a non-string item"
    return None


def unit_candidates(result: dict, number: int) -> tuple[list[dict], list[str], list[str]]:
    """(candidates tagged with their unit, open questions, named overflow notes)."""
    notes = []
    findings = result["findings"]
    if len(findings) > UNIT_MAX_FINDINGS:
        notes.append(f"unit {number}: {len(findings) - UNIT_MAX_FINDINGS} provisional finding(s) beyond the unit "
                     f"limit of {UNIT_MAX_FINDINGS} not carried (unverified)")
    kept_findings, finding_notes = focused.lenient_candidates({"findings": findings[:UNIT_MAX_FINDINGS]})
    notes.extend(f"unit {number}: {n}" for n in finding_notes)
    raw_hypotheses = result["hypotheses"]
    if len(raw_hypotheses) > UNIT_MAX_HYPOTHESES:
        notes.append(f"unit {number}: {len(raw_hypotheses) - UNIT_MAX_HYPOTHESES} hypothesis(es) beyond the unit "
                     f"limit of {UNIT_MAX_HYPOTHESES} not carried (unverified)")
    kept_hypotheses = hypotheses({"hypotheses": raw_hypotheses[:UNIT_MAX_HYPOTHESES]})
    oversized = len(raw_hypotheses[:UNIT_MAX_HYPOTHESES]) - len(kept_hypotheses)
    if oversized > 0:
        notes.append(f"unit {number}: {oversized} hypothesis(es) exceeded the per-item size bound (unverified)")
    candidates = [{**item, "kind": item.get("kind", "candidate"), "unit": number} for item in kept_findings]
    candidates += [{**item, "kind": "hypothesis", "unit": number} for item in kept_hypotheses]
    # evidence-v24: every open question is kept (bounded text) and carried to the final payload, where the
    # exact final budget decides; one trimmed there is named.
    questions = [f"unit {number}: {str(q)[:480]}" for q in result["coverage_gaps"]]
    return candidates, questions, notes


def describe(item: dict) -> str:
    title = item.get("title") or item.get("trigger") or item.get("mechanism") or "untitled"
    return f"{item.get('path', '?')} — {str(title)[:120]}"


def merge_candidates(per_unit: list[list[dict]]) -> tuple[list[dict], list[str]]:
    """Round-robin across units (each in its own order) into CANDIDATE_LIST_MAX_CHARS; every candidate not
    carried to the final verification is named."""
    merged, longest = [], max((len(c) for c in per_unit), default=0)
    for position in range(longest):
        for candidates in per_unit:
            if position < len(candidates):
                merged.append(candidates[position])
    items, notes, used = [], [], 0
    for item in merged:
        size = len(json.dumps(item, ensure_ascii=False))
        if used + size > focused.CANDIDATE_LIST_MAX_CHARS:
            notes.append(f"candidate not carried to verification (envelope full): {describe(item)} (unverified)")
            continue
        items.append(item)
        used += size
    return items, notes


class unit_work_deadline:
    """Within this block every model with a review deadline uses one FINAL_TIME_HOLD_SECONDS earlier; the
    real deadline is restored afterwards for the final verification. Models without a deadline (offline
    tools and tests) are untouched."""
    def __init__(self, *models):
        # dict.fromkeys de-duplicates by identity-equal objects: a model that is also the verifier is
        # shifted once and restored once.
        self.models = [m for m in dict.fromkeys(models) if getattr(m, "deadline", None) is not None]
        self.saved = None

    def __enter__(self):
        if self.saved is None:  # idempotent: entering again never shifts twice
            self.saved = [(m, m.deadline) for m in self.models]
            for model, deadline in self.saved:
                model.deadline = deadline - FINAL_TIME_HOLD_SECONDS
        return self

    def __exit__(self, *exc):
        if self.saved is not None:
            for model, deadline in self.saved:
                model.deadline = deadline
            self.saved = None
        return False


def unsent_after_reservation(budget: Budget, calls_before: int) -> bool:
    """A reservation the provider settled to a known zero because the request was never sent (its deadline
    check after reserving). Nothing was charged, so the unit is simply not audited."""
    if len(budget.calls) <= calls_before:
        return False
    call = budget.calls[-1]
    return call.get("sent") is False and call.get("billing_known") is True and call.get("actual_usd") == 0


def final_supplied_paths(package: dict) -> set[str]:
    """The paths the final verifier was actually shown: its admitted hunks and evidence windows. Never the
    whole inventory: a rejection or support may only cite code that was in the final package."""
    paths = {h.get("path") for h in package.get("hunks", []) if isinstance(h, dict)}
    paths |= {w.get("path") for w in package.get("windows", []) if isinstance(w, dict)}
    return {p for p in paths if isinstance(p, str) and p}


def budget_fields(budget: Budget) -> dict:
    """The budget report and the top-level model cap taken from that same report. A publisher DurableBudget's
    cap is the all-in scope cap; its report's model_cap_usd is the model cap (all-in less runner allowances),
    which is what a review states. Never budget.cap."""
    report = budget.report()
    return {"budget": report, "model_cap_usd": report["model_cap_usd"]}


def named(prefix: str, paths: list[str]) -> list[str]:
    out = [f"{prefix}: {p}" for p in paths[:MAX_NAMED_GAPS]]
    if len(paths) > MAX_NAMED_GAPS:
        out.append(f"{prefix}: {len(paths) - MAX_NAMED_GAPS} further file(s)")
    return out


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------

def review_units(context: dict, model: Model, read_file, budget: Budget | None = None,
                 model_cap: int = UNITS_MODEL_CAP_MICRODOLLARS, effort: str = "medium",
                 verifier: Model | None = None, doc_registry: DocRegistry | None = None) -> dict:
    verifier = verifier or model
    budget = budget if budget is not None else Budget(cap=model_cap)
    started = time.monotonic()
    inventory = focused.strip_added_head_content(context)
    base = {"version": VERSION, "experiment": EXPERIMENT, "experiment_prompt_version": EXPERIMENT_PROMPT_VERSION,
            "variant": EXPERIMENT, "model": model.model, "verification_model": verifier.model, "effort": effort,
            "repository": inventory["repository"], "number": inventory["number"],
            "head_sha": inventory["head_sha"], "base_sha": inventory["base_sha"]}
    coverage = inventory.get("coverage", {})
    scope_notes = [*coverage.get("info", []), *coverage.get("notes", [])]
    coverage_gaps = list(coverage.get("notes", [])) if not coverage.get("complete", False) else []
    if coverage.get("omitted_files"):
        coverage_gaps.append(f"{len(coverage['omitted_files'])} changed file(s) omitted from the review inventory")
    routing = route(inventory)
    units = routing["units"]
    units_log = [{"index": n + 1, "role": routing["roles"][n], "files": u, "kinds": sorted({route_kind(p) for p in u}),
                  "required_usd": None, "planned": False, "audited": False, "budget_call": None,
                  "result_problem": None, "candidates": 0, "requests": 0, "answered": 0,
                  "followups": [], "read_status": []}
                 for n, u in enumerate(units)]
    stages, extra, retrieval_notes, answered_all, questions = [], [], [], [], []
    read_paths = set()
    diagnostics = {"routing": {"units": len(units), "primary": routing["roles"].count("primary"),
                               "deferred": routing["roles"].count("deferred"), "promoted": routing["promoted"],
                               "unreviewable": routing["unreviewable"], "notes": routing["notes"]},
                   "units": units_log, "verification": None, "read_paths": []}
    scope_notes.extend(routing["notes"])
    # evidence-v24: final-only dispositions (root's dispositions.py): one per controller-assigned candidate ID.
    final_instruction = (VERIFY + focused.FOCUSED_VERIFY + focused.DOCUMENTATION_VERIFY + DOCUMENTATION_RUNTIME_BASIS
                         + VERIFICATION_DISPOSITIONS + QUESTION_INSTRUCTION)
    # The same fixed-ID documentation layer as the focused pipeline: catalog in the unit instruction,
    # at most 2 distinct IDs per review (admit_doc_requests over one shared admitted list), untrusted data.
    registry = doc_registry if doc_registry is not None else DocRegistry()
    unit_instruction = (UNIT_AUDIT + focused.DOCUMENTATION_ADDENDUM.replace("{catalog}", registry.catalog() or "- (none)")
                        + DOCUMENTATION_RUNTIME_BASIS)
    def followup_instruction(round_number: int) -> str:
        return (UNIT_FOLLOWUP.replace("{round}", str(round_number)).replace("{rounds}", str(MAX_FOLLOWUP_ROUNDS))
                + UNIT_FOLLOWUP_DOCS + DOCUMENTATION_RUNTIME_BASIS)
    doc_log, doc_ids, doc_notes = [], [], []
    work_deadline = unit_work_deadline(model, verifier)
    try:
        if not units:
            raise BudgetExceeded("No reviewable unit: no changed file has a patch; no generation sent")
        # 0. The only forward hold: the exact preflight of an empty final verification plus a margin.
        empty_package, _, _, empty_failures = focused.gather_evidence(inventory, [], [], read_file)
        if empty_failures:
            raise ReviewUnavailable("Temporary context failure; stopped before any generation")
        final_floor = verifier.required_budget(POLICY, {"candidates_and_hypotheses": []}, "verification",
                                               cache_context=empty_package, instruction=final_instruction,
                                               output_tokens=focused.VERIFICATION_MIN_OUTPUT) + FINAL_MARGIN_MICRODOLLARS
        diagnostics["final_floor_usd"] = final_floor / 1e6
        # Documentation revision 2: one deterministic selection per review, before the first unit envelope, from
        # the routed source/config files (every patched file when none, as the router's promotion does). The
        # picks share the 2 slots with later requests and go to the final verification package.
        routed_primary = {p for u, r in zip(units, routing["roles"]) for p in u if route_kind(p) in PRIMARY_KINDS}
        eligible_files = [f for f in inventory.get("files", []) if f["path"] in routed_primary and f.get("patch")] \
            or [f for f in inventory.get("files", []) if f.get("patch")]
        auto_log = []
        auto_documents = select_documents(registry, eligible_files, doc_ids, auto_log)
        auto_ids = tuple(doc_ids)
        doc_log.extend(auto_log)
        diagnostics["documentation_auto"] = {
            "selected": list(auto_ids),
            "by_basis": {b: sum(1 for e in auto_log if e.get("status") == "auto_selected" and e.get("basis") == b)
                         for b in ("api", "runtime")},
            "eligible_not_selected": [e.get("doc") for e in auto_log if e.get("status") == "auto_eligible_not_selected"]}
        # 1. Exact counts of every unit (no generation), under the earlier unit-work deadline.
        work_deadline.__enter__()
        envelopes, required, count_timed_out, left_out_snippets = [], [], set(), {}
        for index in range(len(units)):
            envelope = unit_evidence(inventory, routing, index)
            left_out = envelope.pop("context_left_out")
            left_out_snippets[index] = left_out
            # M6: a snippet not attached to the unit envelope is incidental here; it is offered to the final
            # verification (below), where an exclusion that matters to a carried candidate is a named gap.
            scope_notes.extend(f"unit {index + 1}: collector snippet {s.get('path')} lines {s.get('start_line')}-"
                               f"{s.get('end_line')} not attached (unit context bound); offered to the final "
                               "verification" for s in left_out)
            envelopes.append(envelope)
            try:
                need = model.required_budget(DISCOVERY_POLICY, {}, "discovery", cache_context=envelopes[index],
                                             instruction=unit_instruction, output_tokens=UNIT_MAX_OUTPUT)
            except DeadlineReached:
                need = None
                count_timed_out.add(index)
            except BudgetExceeded:
                need = None
            required.append(need)
            units_log[index]["required_usd"] = None if need is None else need / 1e6
        primary = [i for i, r in enumerate(routing["roles"]) if r == "primary"]
        deferred = [i for i, r in enumerate(routing["roles"]) if r == "deferred"]
        # 2. Primary units: the priority prefix that fits up front with the final floor.
        planned, total = [], 0
        for index in primary:
            if required[index] is not None and total + required[index] + final_floor <= budget.remaining:
                planned.append(index)
                total += required[index]
                units_log[index]["planned"] = True
            else:
                reason = "time" if index in count_timed_out else "budget"
                coverage_gaps.extend(named(f"primary unit {index + 1} not audited ({reason})", units[index]))
        if primary and not planned:
            raise BudgetExceeded("No primary unit fits with the final verification floor; no generation sent")
        results, followups, out_of_time = {}, {}, []
        unit_answers = {i: [] for i in range(len(units))}
        answer_cache, read_pages = {}, [0]
        read_log = {"reads": 0, "cached": 0, "seconds": 0.0, "max_read_seconds": 0.0, "allowance_overruns": 0,
                    "not_started_time": []}
        source_cache = {}

        def guarded_read(minimum_left):
            """read_file, cached by its immutable arguments, and started only with `minimum_left` seconds before
            the real deadline. That is an admission rule, not a bound on the read's own duration (see
            READ_TIMEOUT_SECONDS). A read that may not start returns an error result and is a named time gap."""
            def read(repository, sha, path, start, end):
                key = (repository, sha, path, start, end)
                if key in source_cache:
                    read_log["cached"] += 1
                    return source_cache[key]
                left = real_time_left()
                if left is not None and left < minimum_left:
                    read_log["not_started_time"].append(f"{path}:{start}-{end}")
                    return {"error": "not read (time)"}
                began = time.monotonic()
                value = read_file(repository, sha, path, start, end)
                elapsed = time.monotonic() - began
                read_log["reads"] += 1
                read_log["seconds"] += elapsed
                read_log["max_read_seconds"] = max(read_log["max_read_seconds"], elapsed)
                if elapsed > READ_TIMEOUT_SECONDS:
                    read_log["allowance_overruns"] += 1  # measured: the allowance did not cover this read
                if isinstance(value, dict) and not str(value.get("error", "")).startswith("read failed"):
                    source_cache[key] = value
                return value
            return read

        unit_reader = guarded_read(FINAL_TIME_HOLD_SECONDS + READ_TIMEOUT_SECONDS)
        final_reader = guarded_read(VERIFICATION_COUNT_SECONDS + VERIFICATION_MIN_USEFUL_SECONDS + READ_TIMEOUT_SECONDS)

        def settled_output_failure(error, calls_before) -> bool:
            """Only the provider's InvalidModelOutput (sent, settled at the requested tier's price, output
            unusable) on a call this unit made. Unknown usage or tier, a billing overrun, a transport or a
            preflight failure is a plain ReviewUnavailable and still stops the review."""
            return (isinstance(error, InvalidModelOutput) and len(budget.calls) > calls_before
                    and budget.calls[-1].get("billing_known") is True)

        def cumulative(index):
            """One unit's candidates and open questions over its audit and every follow-up round: the newest
            version of a candidate leads, and earlier candidates a later round did not restate are carried."""
            found, questions_now, notes = unit_candidates(results[index], index + 1)
            for result in followups.get(index, []):
                newer, newer_questions, newer_notes = unit_candidates(result, index + 1)
                notes = notes + newer_notes
                found, merge_notes = focused.merge_items(newer, found, focused.CANDIDATE_LIST_MAX_CHARS)
                notes = notes + [f"unit {index + 1}: {n}" for n in merge_notes]
                questions_now = list(dict.fromkeys([*newer_questions, *questions_now]))
            return found, questions_now, notes

        def evidence_tiers() -> tuple[list[dict], int]:
            """phase-v25: (ordered material evidence, requested_count). The requested tier is every answered
            request and requested document, plus an automatically selected document a unit also requested (its
            request was answered by the automatic copy, so it is requested proof); then the other automatic
            documents in selection order."""
            asked = {e.get("doc") for e in doc_log if e.get("status") == "already_supplied"}
            automatic = [d for d in auto_documents if d not in extra]
            promoted = [d for d in automatic if (d.get("request") or {}).get("doc") in asked]
            requested_tier = [*extra, *promoted]
            return [*requested_tier, *[d for d in automatic if d not in promoted]], len(requested_tier)

        def final_hold() -> int:
            """Exact preflight, at the minimum output plus the margin, of the mandatory final verification as it
            would be sent now: the candidates with controller IDs, every open question, the answered requests
            and documents and the automatic documents (known material evidence), assembled like the final
            package. Collector snippets are optional and not held. Never below the empty floor. Taken before
            every optional paid call, so later work cannot starve the final proof of what is already known."""
            per = [cumulative(i) for i in sorted(results)]
            items_now = identify_candidates(merge_candidates([found for found, _, _ in per])[0])
            if not items_now:
                return final_floor
            questions_now = [q for _, found_questions, _ in per for q in found_questions][:MAX_OPEN_QUESTIONS]
            # phase-v25: the same tiers and order as the final package (requested first, smallest marginal size).
            known, requested_now = evidence_tiers()
            base_now, entries_now, _, failures_now = focused.gather_evidence(inventory, items_now, known, unit_reader,
                                                                             requested_count=requested_now)
            if failures_now:
                raise ReviewUnavailable("Temporary context failure; stopped before further generation")
            payload_now = {"candidates_and_hypotheses": items_now, "open_questions": questions_now,
                           "answered_requests": answered_all[-40:], "retrieval_notes": retrieval_notes[-40:]}
            try:
                need_now = verifier.required_budget(POLICY, payload_now, "verification",
                                                    cache_context=focused.assemble(inventory, base_now, entries_now),
                                                    instruction=final_instruction,
                                                    output_tokens=focused.VERIFICATION_MIN_OUTPUT)
            except BudgetExceeded:
                return budget.remaining + 1  # cannot even be priced in the verified tier: admit nothing more
            return max(final_floor, need_now + FINAL_MARGIN_MICRODOLLARS)

        def real_time_left():
            """Seconds before the real (not the earlier) deadline, or None without a deadline."""
            for candidate in (model, verifier):
                if getattr(candidate, "deadline", None) is not None and callable(getattr(candidate, "clock", None)):
                    shift = FINAL_TIME_HOLD_SECONDS if work_deadline.saved is not None else 0
                    return candidate.deadline + shift - candidate.clock()
            return None

        def read_time_ok() -> bool:
            left = real_time_left()
            return left is None or left >= FINAL_TIME_HOLD_SECONDS + READ_TIMEOUT_SECONDS

        def record_reads(index, entries):
            added = 0
            for item in entries:
                if item not in extra:
                    extra.append(item)
                if item not in unit_answers[index]:
                    unit_answers[index].append(item)
                    added += 1
                path = (item.get("request") or {}).get("path") if isinstance(item, dict) else None
                if path:
                    read_paths.add(path)
            return added

        def paged_read(request, number):
            """A read in bounded 200-line pages from the last line actually returned. Metadata comes from each
            read result, never from the request; an incomplete read is a named gap."""
            start_line, end_line = request.get("start"), request.get("end")
            if type(start_line) is not int or type(end_line) is not int or not 1 <= start_line <= end_line:
                reads, notes = retrieve({"requests": [request]}, inventory, unit_reader)  # rejected there, noted
                retrieval_notes.extend(notes)
                return reads, None
            pages, cursor, stopped, last_end, total = [], start_line, None, None, None
            for _ in range(READ_PAGES_PER_REQUEST):
                if read_pages[0] >= REVIEW_MAX_READ_PAGES:
                    stopped = "review read bound"
                    break
                if not read_time_ok():
                    stopped = "time"
                    break
                page_request = {**request, "start": cursor, "end": min(end_line, cursor + 199)}
                reads, notes = retrieve({"requests": [page_request]}, inventory, unit_reader)
                read_pages[0] += 1
                retrieval_notes.extend(notes)
                if any(n.startswith("Context transport failure") for n in notes):
                    raise ReviewUnavailable("Temporary context failure; stopped before further generation")
                if not reads:
                    stopped = "no result"
                    break
                item = reads[0]
                result = item.get("result") if isinstance(item, dict) else None
                pages.append(item)
                if not isinstance(result, dict) or type(result.get("end_line")) is not int:
                    stopped = "error result"
                    break
                last_end, total = result["end_line"], result.get("total_lines")
                if last_end >= end_line or (type(total) is int and last_end >= total):
                    break
                if last_end < cursor:
                    stopped = "no progress"
                    break
                cursor = last_end + 1
            else:
                stopped = "page bound"
            wanted_end = min(end_line, total) if type(total) is int else end_line
            complete = last_end is not None and last_end >= wanted_end
            status = {"path": request.get("path"), "ref": request.get("ref", "head"),
                      "requested": [start_line, end_line], "returned": [start_line, last_end] if last_end else None,
                      "total_lines": total, "complete": complete,
                      "stopped": None if complete else (stopped or "incomplete")}
            if not complete and stopped not in ("error result", "no result"):
                coverage_gaps.append(f"unit {number}: read of {request.get('path')} ({status['ref']}) lines "
                                     f"{start_line}-{end_line} returned {start_line}-{last_end} of {total} "
                                     f"({status['stopped']}) (unverified)")
            return pages, status

        def answer_requests(index, requests) -> int:
            """Free reads for one unit's requests (docs from the fixed registry only). An item another unit
            already requested is shared, never re-read and never withheld. Returns the newly answered count."""
            number = index + 1
            if len(requests) > UNIT_MAX_REQUESTS:
                coverage_gaps.append(f"unit {number}: {len(requests) - UNIT_MAX_REQUESTS} evidence request(s) beyond "
                                     f"the unit limit of {UNIT_MAX_REQUESTS} not answered (unverified)")
            added = 0
            for request in requests[:UNIT_MAX_REQUESTS]:
                units_log[index]["requests"] += 1
                key = json.dumps(request, sort_keys=True, default=str)
                if key in answer_cache:
                    added += record_reads(index, answer_cache[key])
                    continue
                status = None
                if is_doc_request(request):
                    entries, notes = admit_doc_requests(registry, [request], doc_ids, doc_log, number,
                                                        auto_selected=auto_ids)
                    doc_notes.extend(notes)
                elif request.get("kind") == "read":
                    entries, status = paged_read(request, number)
                else:
                    if not read_time_ok():
                        coverage_gaps.append(f"unit {number}: {request.get('kind')} request not answered (time) "
                                             "(unverified)")
                        continue
                    entries, notes = retrieve({"requests": [request]}, inventory, unit_reader)
                    retrieval_notes.extend(notes)
                    if any(n.startswith("Context transport failure") for n in notes):
                        raise ReviewUnavailable("Temporary context failure; stopped before further generation")
                if status is not None:
                    units_log[index]["read_status"].append(status)
                answer_cache[key] = entries
                answered_all.append(request)
                added += record_reads(index, entries)
            units_log[index]["answered"] = len(unit_answers[index])
            return added

        def audit(index, ceiling):
            number = index + 1
            calls_before = len(budget.calls)
            try:
                result = model.invoke(DISCOVERY_POLICY, {}, budget, "discovery", max_output=UNIT_MAX_OUTPUT,
                                      minimum_output=UNIT_MIN_OUTPUT, ceiling=ceiling,
                                      cache_context=envelopes[index], instruction=unit_instruction, effort=effort)
            except BudgetExceeded as error:
                if len(budget.calls) > calls_before and not unsent_after_reservation(budget, calls_before):
                    raise  # charged above its reservation: the provider contract changed; stop
                reason = "time" if isinstance(error, DeadlineReached) else "budget at send time"
                if reason == "time":
                    out_of_time.append(number)
                coverage_gaps.extend(named(f"{routing['roles'][index]} unit {number} not audited ({reason})",
                                           units[index]))
                return
            except ReviewUnavailable as error:
                if not settled_output_failure(error, calls_before):
                    raise
                units_log[index]["result_problem"] = str(error)[:120]
                coverage_gaps.extend(named(f"unit {number} output unusable ({str(error)[:80]}); not reviewed",
                                           units[index]))
                return
            finally:
                if len(budget.calls) > calls_before:
                    units_log[index]["budget_call"] = len(budget.calls) - 1
            stages.append({"phase": "unit", "unit": number, "role": routing["roles"][index], "result": result})
            problem = unit_result_problem(result)
            if problem:
                units_log[index]["result_problem"] = problem
                coverage_gaps.extend(named(f"unit {number} returned an unusable result ({problem}); not reviewed",
                                           units[index]))
                return
            units_log[index]["audited"] = True
            results[index] = result

        def follow_up(index, round_number):
            """One paid follow-up round (admitted against the exact final hold and the earlier deadline).
            Returns its result, or None when it did not run (named gap)."""
            number = index + 1
            log = {"round": round_number, "run": False, "budget_call": None, "requests": 0}
            units_log[index]["followups"].append(log)
            own, _, _ = cumulative(index)
            evidence = {**envelopes[index], "unit_candidates": own, "answered_requests": unit_answers[index],
                        "read_status": units_log[index]["read_status"]}
            instruction = followup_instruction(round_number)
            if out_of_time:
                coverage_gaps.append(f"unit {number}: follow-up round {round_number} not run (time); its answered "
                                     "evidence still goes to the final verification (unverified)")
                return None
            try:
                need = model.required_budget(DISCOVERY_POLICY, {}, "investigation", cache_context=evidence,
                                             instruction=instruction, output_tokens=FOLLOWUP_MAX_OUTPUT)
            except DeadlineReached:
                out_of_time.append(number)
                coverage_gaps.append(f"unit {number}: follow-up round {round_number} not run (time); its answered "
                                     "evidence still goes to the final verification (unverified)")
                return None
            except BudgetExceeded:
                need = None
            hold = final_hold()
            if need is None or need + hold > budget.remaining:
                coverage_gaps.append(f"unit {number}: follow-up round {round_number} not run (budget); its answered "
                                     "evidence still goes to the final verification (unverified)")
                return None
            calls_before = len(budget.calls)
            try:
                result = model.invoke(DISCOVERY_POLICY, {}, budget, "investigation", max_output=FOLLOWUP_MAX_OUTPUT,
                                      minimum_output=FOLLOWUP_MIN_OUTPUT, ceiling=budget.remaining - hold,
                                      cache_context=evidence, instruction=instruction, effort=effort)
            except BudgetExceeded as error:
                if len(budget.calls) > calls_before and not unsent_after_reservation(budget, calls_before):
                    raise
                reason = "time" if isinstance(error, DeadlineReached) else "budget at send time"
                if reason == "time":
                    out_of_time.append(number)
                coverage_gaps.append(f"unit {number}: follow-up round {round_number} not run ({reason}) (unverified)")
                return None
            except ReviewUnavailable as error:
                if not settled_output_failure(error, calls_before):
                    raise
                coverage_gaps.append(f"unit {number}: follow-up round {round_number} output unusable "
                                     f"({str(error)[:80]}); earlier candidates are kept (unverified)")
                return None
            finally:
                if len(budget.calls) > calls_before:
                    log["budget_call"] = len(budget.calls) - 1
            stages.append({"phase": "unit-followup", "unit": number, "round": round_number, "result": result})
            problem = unit_result_problem(result)
            if problem:
                coverage_gaps.append(f"unit {number}: follow-up round {round_number} returned an unusable result "
                                     f"({problem}); earlier candidates are kept (unverified)")
                return None
            log["run"] = True
            log["requests"] = len(result["requests"])
            followups.setdefault(index, []).append(result)
            return result

        def evidence_rounds(group):
            """For audited units of one group: answer every unit's requests (free) before any paid follow-up;
            then follow-up rounds round-robin across the group. A round-1 follow-up runs only when the unit has
            answered evidence; round 2 only when round 1's requests produced new answered evidence. Every
            round's requests are answered, the last round's for the final verification only."""
            audited_now = [i for i in group if i in results]
            fresh = {i: answer_requests(i, results[i]["requests"]) for i in audited_now}
            for round_number in range(1, MAX_FOLLOWUP_ROUNDS + 1):
                ran = {}
                for index in audited_now:
                    if fresh.get(index):
                        ran[index] = follow_up(index, round_number)
                fresh = {}
                for index, result in ran.items():
                    if result is not None and result["requests"]:
                        fresh[index] = answer_requests(index, result["requests"])
                if not fresh:
                    break

        # 3-4. All paid unit work runs under the earlier deadline (FINAL_TIME_HOLD_SECONDS kept for the final
        #      verification). Once the deadline stops a paid call, no further paid unit work starts (named
        #      "time" gaps); free reads continue while the real deadline allows them (read_time_ok).
        with work_deadline:
            # 3. Primary audits in priority order; each ceiling preserves the later planned units and the floor.
            for position, index in enumerate(planned):
                if out_of_time:
                    coverage_gaps.extend(named(f"primary unit {index + 1} not audited (time)", units[index]))
                    continue
                later = sum(required[i] for i in planned[position + 1:])
                audit(index, budget.remaining - later - final_floor)
            evidence_rounds(planned)
            # 4. Deferred units, one at a time, from the exact remaining budget (always keeping the hold).
            admitted_deferred = []
            for index in deferred:
                if out_of_time:
                    coverage_gaps.extend(named(f"deferred unit {index + 1} not audited (time)", units[index]))
                    continue
                hold = final_hold()
                if required[index] is None or required[index] + hold > budget.remaining:
                    reason = "time" if index in count_timed_out else "budget"
                    coverage_gaps.extend(named(f"deferred unit {index + 1} not audited ({reason})", units[index]))
                    continue
                units_log[index]["planned"] = True
                audit(index, budget.remaining - hold)
                admitted_deferred.append(index)
            evidence_rounds(admitted_deferred)
        # 5. Every supported candidate, with named overflow, to the mandatory final verification.
        per_unit = []
        for index in sorted(results):
            candidates, unit_questions, notes = cumulative(index)
            coverage_gaps.extend(notes)
            units_log[index]["candidates"] = len(candidates)
            per_unit.append(candidates)
            questions.extend(unit_questions)
        items, merge_notes = merge_candidates(per_unit)
        coverage_gaps.extend(merge_notes)
        second_look = not items
        if second_look:
            # No candidate at all: never ask the verifier to clear an empty package. Offer it the audited code
            # (every hunk of each audited file with a head line) as a second look, if that fits exactly.
            audited_files = [p for i in sorted(results) for p in units[i]
                             if numbered_patch(next(f for f in inventory["files"] if f["path"] == p)["patch"])[1]]
            items, look_notes = merge_candidates([[{
                "path": p, "kind": "second-look", "trigger": "its unit audit raised no provisional candidate",
                "mechanism": "independent second look at the audited change",
                "check": "report only a concrete, reachable defect visible in this code"} for p in audited_files]])
            coverage_gaps.extend(look_notes)
        # Controller-owned candidate IDs, assigned once before the final preflight; trimming keeps them.
        items = identify_candidates(items)
        # M3: collector snippets already shown to (or left out of) an audited unit are offered to the final
        # verification after every requested read, deduplicated by (path, ref, lines), with their ref labelled.
        snippet_entries, seen_snippets = [], set()
        for index in sorted(results):
            for snippet in [*envelopes[index].get("context_files", []), *left_out_snippets.get(index, [])]:
                key = (snippet.get("path"), snippet.get("ref"), snippet.get("start_line"), snippet.get("end_line"))
                if key in seen_snippets or not isinstance(snippet.get("content"), str):
                    continue
                seen_snippets.add(key)
                side = "head" if snippet.get("ref") in ("head", inventory["head_sha"]) else "base"
                sha = inventory["head_sha"] if side == "head" else inventory["base_sha"]
                result = ({"path": snippet["path"], "ref": sha,
                           "windows": [{"start_line": snippet.get("start_line"), "content": snippet["content"]}],
                           "truncated": bool(snippet.get("truncated"))} if snippet.get("line_numbered") else
                          {"path": snippet["path"], "ref": sha, "start_line": snippet.get("start_line"),
                           "content": snippet["content"], "truncated": bool(snippet.get("truncated"))})
                snippet_entries.append({"request": {"kind": "read", "path": snippet["path"], "ref": side,
                                                    "start": snippet.get("start_line"), "end": snippet.get("end_line"),
                                                    "source": "collector snippet"}, "result": result})
        # Order: requested reads and documents, then automatically selected documents, then collector snippets.
        material, requested_count = evidence_tiers()
        final_extra = [*material, *snippet_entries]
        all_questions = questions[:MAX_OPEN_QUESTIONS]
        if len(questions) > MAX_OPEN_QUESTIONS:
            coverage_gaps.extend(f"open question not passed to the final verification (bound): {q[:300]}"
                                 for q in questions[MAX_OPEN_QUESTIONS:])
        # Decision (2): a carried question is not a resolved one. Every unit open question stays a named gap
        # unless explicitly resolved; this round has no per-question resolution, so all stay (conservative).
        question_items = question_records(all_questions)
        final_payload = {"candidates_and_hypotheses": items, "open_questions": question_items,
                         "incremental": inventory.get("incremental"),
                         "earlier_pr_changes": inventory.get("cumulative_context"),
                         "answered_requests": answered_all[-40:], "retrieval_notes": retrieval_notes[-40:]}
        # phase-v25: requested evidence (reads, searches, path lookups and requested documents) is one tier, ordered
        # smallest marginal compacted size first; automatic documents and then collector snippets follow in order.
        guard_excluded = []
        base_package, entries, gather_notes, failures = focused.gather_evidence(inventory, items, final_extra,
                                                                                final_reader,
                                                                                requested_count=requested_count,
                                                                                excluded=guard_excluded)
        retrieval_notes.extend(failures)
        if failures:
            raise ReviewUnavailable("Temporary context failure; stopped before further generation")

        def final_need(candidate_package):
            try:
                return verifier.required_budget(POLICY, final_payload, "verification", cache_context=candidate_package,
                                                instruction=final_instruction,
                                                output_tokens=focused.VERIFICATION_MAX_OUTPUT)
            except BudgetExceeded:
                raise
            except ReviewUnavailable:
                retrieval_notes.append("Requested evidence could not be admitted because token preflight failed")
                raise ReviewUnavailable("Token preflight failed; stopped before further generation") from None

        final, package, exclusion_notes = None, {}, []
        if second_look and not items:
            diagnostics["verification"] = "not run: no candidate and no audited code with a head line"
            scope_notes.append("unit audits raised no candidate; nothing needed verification")
        else:
            try:
                while True:
                    try:
                        package, _, exclusion_notes = focused.fit_evidence(inventory, base_package, entries,
                                                                           final_need, budget.remaining)
                        break
                    except BudgetExceeded:
                        if final_payload["open_questions"]:
                            # Open questions are trimmed (each named) before any candidate is left out.
                            question = final_payload["open_questions"].pop()
                            coverage_gaps.append(f"open question not passed to the final verification (final "
                                                 f"budget): {str(question)[:300]}")
                            continue
                        if second_look or len(items) <= 1:
                            raise
                        # Not even the candidates fit: the last (lowest-priority) candidate is named and left
                        # out, then the package is rebuilt; with one candidate left it fails closed.
                        dropped = items.pop()
                        coverage_gaps.append(f"candidate not carried to verification (final budget): "
                                             f"{describe(dropped)} (unverified)")
                        final_payload["candidates_and_hypotheses"] = items
                        guard_excluded = []
                        base_package, entries, more_notes, failures = focused.gather_evidence(
                            inventory, items, final_extra, final_reader, requested_count=requested_count,
                            excluded=guard_excluded)
                        gather_notes = more_notes
                        if failures:
                            raise ReviewUnavailable("Temporary context failure; stopped before further generation")
            except BudgetExceeded:
                if not second_look:
                    raise  # a real candidate's mandatory verification cannot fit: fail closed
                diagnostics["verification"] = "not run: the second look did not fit the remaining budget"
                scope_notes.append("unit audits raised no candidate; the optional second-look verification did "
                                   "not fit the remaining budget")
            else:
                final = verifier.invoke(POLICY, final_payload, budget, "verification",
                                        max_output=focused.VERIFICATION_MAX_OUTPUT,
                                        minimum_output=focused.VERIFICATION_MIN_OUTPUT, cache_context=package,
                                        instruction=final_instruction, effort=effort)
                diagnostics["verification"] = "second look" if second_look else "candidates"
                stages.append({"phase": "verification", "second_look": second_look, "result": final})
        findings, disposition_records = [], []
        if final is not None:
            findings, invalid = validated_findings(final, inventory)
            coverage_gaps.extend(invalid)
            # Every carried candidate is accounted for; the finding validator is unchanged and authoritative.
            accounted = audit_dispositions(items, final, findings, supplied_paths=final_supplied_paths(package))
            disposition_records = accounted["records"]
            coverage_gaps.extend(accounted["coverage_gaps"])
            coverage_gaps.extend(gaps(final))
            if hypotheses(final):
                coverage_gaps.append("Verification left unresolved hypotheses")
            if final.get("requests"):
                coverage_gaps.append("Reviewer requested context beyond the bounded verification pass")
        question_decisions, question_gaps = audit_questions(question_items, final, findings,
                                                             final_supplied_paths(package))
        coverage_gaps.extend(question_gaps)
        coverage_gaps.extend(gather_notes)
        # M6/C7: an excluded collector snippet is a scope note only when nothing carried, requested or asked
        # cites its file; every other exclusion (requested reads, cited snippets) stays a named gap.
        excluded = entries[len(entries) - len(exclusion_notes):] if exclusion_notes else []
        excluded_evidence = [*guard_excluded, *(focused.evidence_descriptor(e, "final budget (exact fit)")
                                                for e in excluded)]
        patches = {f["path"]: f.get("patch") for f in inventory.get("files", []) if f.get("patch")}
        cited = {i.get("path") for i in items if isinstance(i.get("path"), str)}
        cited |= {p for i in items for p, _, _ in focused.mentioned(i, patches)}
        cited |= {r.get("path") for r in answered_all if isinstance(r, dict) and isinstance(r.get("path"), str)}
        question_text = " ".join(q["question"] for q in final_payload["open_questions"])
        for entry, note in zip(excluded, exclusion_notes):
            request = entry.get("request") or {}
            path = request.get("path")
            if request.get("source") == "collector snippet" and path not in cited \
                    and not (isinstance(path, str) and path in question_text):
                scope_notes.append(f"collector snippet {path} lines {request.get('start')}-{request.get('end')} "
                                   "not included in the final verification (budget); no candidate, request or "
                                   "question cites it")
            elif request.get("source") == "collector snippet":
                coverage_gaps.append(f"collector snippet {path} ({request.get('ref')}) lines {request.get('start')}-"
                                     f"{request.get('end')} excluded: the final verification would not fit; it is "
                                     "cited by a carried candidate, request or question (unverified)")
            else:
                coverage_gaps.append(note)
        if read_log["not_started_time"]:
            coverage_gaps.extend(f"source read not started (time): {r}" for r in read_log["not_started_time"][:40])
        coverage_gaps.extend(n for n in retrieval_notes if n.startswith("Requested evidence"))
        coverage_gaps.extend(doc_notes)
        # 6. Honest coverage: every file outside an audited unit is named; deletions cannot anchor findings.
        audited = {p for i in results for p in units[i]}
        coverage_gaps.extend(named("changed file has no patch in the inventory (not reviewed)", routing["unreviewable"]))
        for record in inventory.get("files", []):
            if record["path"] in audited and not numbered_patch(record["patch"])[1]:
                coverage_gaps.append(f"{record['path']}: no head line is shown (deleted or deletion-only patch); "
                                     "audited for lost behavior, but no finding can anchor in it")
        diagnostics["read_paths"] = sorted(read_paths)
        diagnostics["reads"] = {**read_log, "pages": read_pages[0], "seconds": round(read_log["seconds"], 3),
                                "max_read_seconds": round(read_log["max_read_seconds"], 3),
                                "not_started_time": read_log["not_started_time"][:40],
                                "snippets_offered": len(snippet_entries)}
        diagnostics["stopped_by_time_at_unit"] = out_of_time[0] if out_of_time else None
        coverage_gaps = list(dict.fromkeys(coverage_gaps))
        complete = bool(coverage.get("complete", False)) and not coverage_gaps
        return {**base, "status": "complete" if complete else "partial", "findings": findings, "retryable": False,
                "coverage_gaps": coverage_gaps, "scope_notes": scope_notes, "units": diagnostics,
                "documentation": {"catalog": sorted(registry.records), "unavailable": registry.unavailable,
                                  "config_error": registry.config_error, "requests": doc_log, "supplied": doc_ids,
                                  "in_final_package": [d["doc"] for d in focused.describe_evidence(
                                      package.get("windows", [])) if d.get("kind") == "doc"]},
                "verified_items": len(items), "retrievals": len(extra), "retrieval_notes": retrieval_notes,
                "audit": {"items": items, "dispositions": disposition_records,
                          "question_resolutions": question_decisions,
                          "package_chars": len(json.dumps(package, ensure_ascii=False)),
                          "admitted_evidence": focused.describe_evidence(package.get("windows", [])),
                          "admitted_hunks": focused.describe_hunks(package, inventory),
                          "excluded_evidence": excluded_evidence,
                          "additional_context": extra, "stages": stages},
                "summary": str((final or {}).get("summary", ""))[:500],
                **budget_fields(budget), "elapsed_seconds": round(time.monotonic() - started, 3)}
    except ReviewUnavailable as error:
        return {**base, "status": "unavailable", "findings": [],
                "retryable": any(n.startswith("Context transport failure") or "token preflight failed" in n
                                 for n in retrieval_notes),
                "retrieval_notes": retrieval_notes, "coverage_gaps": [*coverage_gaps, str(error)],
                "units": diagnostics, "documentation": {"requests": doc_log, "supplied": doc_ids,
                                                        "config_error": registry.config_error},
                "audit": {"stages": stages}, **budget_fields(budget),
                "elapsed_seconds": round(time.monotonic() - started, 3)}

    except Exception as error:  # noqa: BLE001 - an unforeseen bug must not lose the paid budget record
        # No retry, no refund, no continuation: the class name only (never the message, which could hold
        # provider or source text), with the budget report and the stage diagnostics as they stand.
        return {**base, "status": "unavailable", "findings": [], "retryable": False,
                "coverage_gaps": [*coverage_gaps, f"Reviewer failed unexpectedly ({type(error).__name__})"],
                "units": diagnostics, "audit": {"stages": [{"phase": s.get("phase"), "unit": s.get("unit")}
                                                           for s in stages]},
                **budget_fields(budget), "elapsed_seconds": round(time.monotonic() - started, 3)}
    finally:
        work_deadline.__exit__(None, None, None)  # the real deadline is always restored


# Runtime scope adapter: the publisher and evaluator use exactly the same entry.
INVENTORY_MAX_CHARS = 450000
SCOPE_INSTRUCTION = """
In incremental mode the primary patches are the new delta. earlier_pr_changes is
context for contracts and broader effects, not a separate reporting surface.
Only report a defect introduced or exposed by the primary delta. Every source
read remains pinned to the supplied base (previous reviewed head) or current head.
"""
DISCOVERY_POLICY += SCOPE_INSTRUCTION
POLICY = POLICY.replace("with exactly these fields:", "with these fields and question_resolutions in verification:")
POLICY += SCOPE_INSTRUCTION + QUESTION_INSTRUCTION


def review_context(context, model, read_file, budget=None, config=None, variant=None):
    # Repository API calls share the same monotonic deadline as model calls.
    with source_deadline(deadline=getattr(model, "deadline", None)):
        return review_units(context, model, read_file, budget, effort="medium")



UNIT_AUDIT += '\nFor each changed value or condition that controls a result, an action, feedback or\na return path, privately enumerate the ordinary states and the outcome returned\nfor each. Check successful and inactive states as carefully as explicit errors.\nTrace that returned value to its consumer before accepting the interpretation.\nFollow navigation/action entry and exit paths and the next invocation of stateful\noperations. Do not assume an available recovery path makes an incorrect outcome\nacceptable. Put only concrete contradictions or decisive questions in the answer.\n'
UNIT_FOLLOWUP += '\nFor each changed value or condition that controls a result, an action, feedback or\na return path, privately enumerate the ordinary states and the outcome returned\nfor each. Check successful and inactive states as carefully as explicit errors.\nTrace that returned value to its consumer before accepting the interpretation.\nFollow navigation/action entry and exit paths and the next invocation of stateful\noperations. Do not assume an available recovery path makes an incorrect outcome\nacceptable. Put only concrete contradictions or decisive questions in the answer.\n'
EXPERIMENT = "D14-outcome-table"
EXPERIMENT_PROMPT_VERSION = "D14-outcome-table-2026-11-13.1"
