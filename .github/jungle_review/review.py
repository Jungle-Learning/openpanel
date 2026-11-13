"""Bounded discovery + adversarial verification. No publication in this module."""
from __future__ import annotations

import hashlib
import json
import re
import time

from provider import Budget, BudgetExceeded, Model, ReviewUnavailable
from prompts import DISCOVERY, INVESTIGATE, POLICY, VERIFY, VERSION


# The validated claim a fingerprint binds. Confidence is excluded: it is a score, not the claim.
CLAIM_FIELDS = ("path", "line", "priority", "title", "body", "evidence", "counterargument")
SCOPE_SHA = re.compile(r"[0-9a-f]{40}")  # used with fullmatch: no trailing newline


def fingerprint(finding: dict, scope: dict) -> str:
    """A new finding's identity: its full validated claim text plus the immutable review scope (head and
    merge base). Two findings share an identity only when the same claim is made on the same scope, so a
    publisher match on an open identity means either an exact repeat of that claim on that scope or a prior
    identifier the model explicitly reused and validated_findings accepted. Nothing here compares titles or
    lines across claims. Earlier signed identifiers stay valid: they are only looked up, never recomputed."""
    head, base = scope.get("head_sha"), scope.get("base_sha")
    if not (isinstance(head, str) and SCOPE_SHA.fullmatch(head) and isinstance(base, str) and SCOPE_SHA.fullmatch(base)):
        raise ReviewUnavailable("Review scope missing or malformed; findings cannot be identified")
    canonical = json.dumps({"identity": "jungle-review-claim-v1", "head": head, "base": base,
                            "claim": {field: finding[field] for field in CLAIM_FIELDS}},
                           sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:24]


def lf_lines(text: str) -> list[str]:
    """Split on LF only, as Git numbers lines (str.splitlines also breaks on form feed, U+2028 and
    other separators, shifting every later coordinate). A final newline adds no phantom line."""
    rows = text.split("\n")
    if rows and rows[-1] == "":
        rows.pop()
    return rows


def numbered_patch(patch: str) -> tuple[str, set[int]]:
    """Expose exact head coordinates, including context beside deleted lines."""
    lines, anchors = [], set()
    old = new = old_left = new_left = 0
    for row in lf_lines(patch):
        if not old_left and not new_left:
            match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", row)
            if match:
                old, old_left, new, new_left = (int(match[1]), int(match[2] or 1),
                                                 int(match[3]), int(match[4] or 1))
            lines.append(row)
        elif row.startswith("+") and new_left:
            lines.append(f"R{new} +{row[1:]}")
            anchors.add(new)
            new += 1
            new_left -= 1
        elif row.startswith("-") and old_left:
            lines.append(f"L{old} -{row[1:]}")
            old += 1
            old_left -= 1
        elif row.startswith(" ") and old_left and new_left:
            lines.append(f"R{new}  {row[1:]}")
            anchors.add(new)
            old += 1
            new += 1
            old_left -= 1
            new_left -= 1
        else:
            lines.append(row)
    return "\n".join(lines), anchors


def provisional(result: dict) -> list[dict]:
    """Keep bounded hypotheses for verification, even with an imprecise anchor.

    Publication has a separate strict validator. Dropping a tentative finding
    here used to hide its evidence from the verifier before it could fix a line.
    """
    raw = result.get("findings", [])
    if not isinstance(raw, list):
        raise ReviewUnavailable("Invalid provisional findings envelope")
    if any(not isinstance(item, dict) or not isinstance(item.get("path"), str)
           or not isinstance(item.get("title"), str) for item in raw):
        raise ReviewUnavailable("Invalid provisional finding identity")
    return [item for item in raw[:10] if len(json.dumps(item)) <= 8000]


def hypotheses(result: dict) -> list[dict]:
    raw = result.get("hypotheses", [])
    if not isinstance(raw, list):
        raise ReviewUnavailable("Invalid hypothesis envelope")
    return [item for item in raw[:12] if isinstance(item, dict) and len(json.dumps(item)) <= 4000]


def merge_proposals(previous, current):
    by_key = {(item.get("path"), item.get("title")): item for item in previous}
    for item in current:
        key = (item.get("path"), item.get("title"))
        by_key.pop(key, None)
        by_key[key] = item
    return list(by_key.values())[-12:]


def compact_context(extra: list[dict]) -> list[dict]:
    """Send overlapping immutable source lines once, preserving coordinates.

    Raw tool receipts remain in the local audit. This only removes duplicate
    evidence from the model input; failures and truncated reads stay explicit.
    """
    files, other = {}, []
    for entry in extra:
        value = entry.get("result")
        if not isinstance(value, dict) or "error" in value:
            other.append(entry)
            continue
        key = (value.get("path"), value.get("ref"))
        if not all(isinstance(v, str) for v in key):
            other.append(entry)
            continue
        windows = value.get("windows")
        if windows is None and isinstance(value.get("content"), str):
            windows = [{"start_line": value.get("start_line"), "content": value["content"]}]
            numbered = False
        elif isinstance(windows, list):
            numbered = True
        else:
            other.append(entry)
            continue
        record = files.setdefault(key, {"path": key[0], "ref": key[1], "lines": {}, "truncated": False})
        record["truncated"] |= bool(value.get("truncated"))
        for window in windows:
            if type(window.get("start_line")) is not int or not isinstance(window.get("content"), str):
                continue
            for offset, row in enumerate(lf_lines(window["content"])):
                line = window["start_line"] + offset
                if numbered:
                    match = re.match(r"^(\d+): (.*)$", row)
                    if not match:
                        continue
                    line, row = int(match[1]), match[2]
                record["lines"].setdefault(line, row)
        if not windows:
            other.append(entry)  # preserve a literal search's zero-match evidence
    for record in files.values():
        record["content"] = "\n".join(f"{n}: {row}" for n, row in sorted(record.pop("lines").items()))
    return [*(f for f in files.values() if f["content"]), *other]


def validated_findings(result: dict, context: dict, minimum_confidence=0.9) -> tuple[list[dict], list[str]]:
    raw = result.get("findings")
    if not isinstance(raw, list) or len(raw) > 20:
        raise ReviewUnavailable("Invalid findings envelope")
    anchors = {f["path"]: (numbered_patch(f["patch"])[1] if f.get("patch") else
                          set(f.get("right_side_lines", f["changed_lines"]))) for f in context["files"]}
    if "publishable_lines" in context:
        anchors = {path: lines.intersection(context["publishable_lines"].get(path, []))
                   for path, lines in anchors.items()}
    existing = {f.get("fingerprint"): f
                for f in context.get("existing_findings", {}).get("findings", [])}
    valid, rejected = [], []
    by_claim, held, effective = {}, {}, set()  # claim identity -> record; prior ID -> claim; published IDs
    for item in raw:
        if not isinstance(item, dict):
            rejected.append("Reviewer emitted an invalid finding")
            continue
        path, line = item.get("path"), item.get("line")
        confidence = item.get("confidence")
        texts = {k: item.get(k) for k in ("title", "body", "counterargument")}
        evidence = item.get("evidence")
        okay = (isinstance(path, str) and path in anchors and type(line) is int
                and line in anchors[path] and item.get("priority") in ("P0", "P1", "P2")
                and type(confidence) in (float, int) and minimum_confidence <= confidence <= 1
                and all(isinstance(v, str) and v.strip() for v in texts.values())
                and len(texts.get("title") or "") <= 100
                and len(texts.get("body") or "") <= 1600
                and isinstance(evidence, list) and 0 < len(evidence) <= 8
                and all(isinstance(v, str) and 0 < len(v) <= 700 for v in evidence))
        if not okay:
            rejected.append("A proposed finding failed confidence, evidence, or changed-line validation")
            continue
        clean = {"path": path, "line": line, "priority": item["priority"],
                 "confidence": confidence, **texts, "evidence": evidence}
        claim = fingerprint(clean, context)  # the full-claim identity, used only to collapse identical claims
        # Whether two findings share a root cause is the model's decision (existing_fingerprint); the
        # host only checks that the claim stays on the open comment it names: an authenticated prior
        # identifier, the same file and priority, and the exact line where GitHub now shows that comment
        # on this head (current_line). An outdated comment (no current line), a moved anchor or a changed
        # priority refuses reuse and the finding is posted as new: a duplicate, never a hidden bug.
        prior_key = item.get("existing_fingerprint")
        prior = existing.get(prior_key, {}) if isinstance(prior_key, str) else {}
        current = prior.get("current_line")
        reusable = (bool(prior) and prior.get("path") == path and prior.get("priority") == item["priority"]
                    and type(current) is int and not isinstance(current, bool) and current == line)
        record = by_claim.get(claim)
        if record is not None:
            # The same claim again: one finding. If the earlier copy did not reuse a prior identifier and this
            # copy names a valid one no other claim holds, the earlier record takes that reuse.
            if record.get("reuse") != "accepted" and reusable and prior_key not in held:
                held[prior_key] = claim
                record["fingerprint"], record["reuse"] = prior_key, "accepted"
            continue
        if reusable and prior_key not in held:
            held[prior_key] = claim
            clean["fingerprint"], clean["reuse"] = prior_key, "accepted"
        else:
            # Another claim already holds that prior identifier (a distinct mechanism naming the same comment),
            # or the reuse is invalid: this claim stays new under its own identity.
            clean["fingerprint"] = claim
            if isinstance(prior_key, str):
                clean["reuse"] = "refused"
        if clean["fingerprint"] in effective:
            continue  # defensive: an effective identity is published once
        effective.add(clean["fingerprint"])
        by_claim[claim] = clean
        valid.append(clean)
    return sorted(valid, key=lambda f: (f["priority"], -f["confidence"]))[:5], list(dict.fromkeys(rejected))


def gaps(result: dict) -> list[str]:
    raw = result.get("coverage_gaps", [])
    if not isinstance(raw, list) or any(not isinstance(s, str) for s in raw):
        raise ReviewUnavailable("Invalid coverage envelope")
    return [s[:500] for s in raw[:6]]


def retrieve(result, context, read_file):
    from context import is_safe_path
    requests = result.get("requests", [])
    if not isinstance(requests, list):
        raise ReviewUnavailable("Invalid context request envelope")
    extra, notes = [], []
    for req in requests[:6]:
        if not isinstance(req, dict):
            notes.append("Rejected malformed context request")
            continue
        path, ref = req.get("path"), req.get("ref")
        kind, query = req.get("kind", "read"), req.get("query", "")
        # Providers sometimes echo the exact immutable commit from the evidence
        # instead of its alias. Accept only these two already frozen revisions.
        if ref == context["head_sha"]:
            ref = "head"
        elif ref == context["base_sha"]:
            ref = "base"
        if ref not in ("head", "base"):
            notes.append("Rejected invalid context revision")
            continue
        if kind != "find_path" and not is_safe_path(path):
            notes.append("Rejected an out-of-scope context request")
            continue
        sha = context[f"{ref}_sha"]
        try:
            if kind == "read":
                start, end = req.get("start"), req.get("end")
                if type(start) is not int or type(end) is not int or not 1 <= start <= end:
                    notes.append("Rejected invalid source range")
                    continue
                value = read_file(context["repository"], sha, path, start, min(end, start + 199))
            elif kind in ("search", "find_path"):
                if not isinstance(query, str) or not 2 <= len(query) <= 120:
                    notes.append("Rejected invalid context search")
                    continue
                if kind == "search":
                    from context import search_context_file
                    value = search_context_file(context["repository"], sha, path, query)
                else:
                    from context import discover_file_paths
                    value = discover_file_paths(context["repository"], sha, query)
            else:
                notes.append("Rejected unsupported context operation")
                continue
            if len(json.dumps(value, ensure_ascii=False)) > 12000:
                notes.append("Requested context exceeded retrieval limit")
                continue
            extra.append({"request": req, "result": value})
            if isinstance(value, dict) and str(value.get("error", "")).startswith("read failed"):
                notes.append(f"Context transport failure for {path or query}")
        except (ValueError, OSError, RuntimeError):
            notes.append(f"Context transport failure for {path or query}")
    return extra, notes


def review_context(context: dict, model: Model, read_file, budget: Budget | None = None,
                   variant="two-pass-v1") -> dict:
    budget = budget if budget is not None else Budget()
    started = time.monotonic()
    discovery_effort = "high" if variant == "deeper-v6" and model.model == "gpt-6.1-sol" else "medium"
    verifier = Model("gpt-6.1-sol") if variant == "hybrid-v7" and model.model == "claude-opus-5-5" else model
    base = {"version": VERSION, "variant": variant, "model": model.model, "effort": "medium",
            "discovery_effort": discovery_effort,
            "verification_model": verifier.model,
            "repository": context["repository"], "number": context["number"],
            "head_sha": context["head_sha"], "base_sha": context["base_sha"]}
    coverage = context.get("coverage", {})
    scope_notes = [*coverage.get("info", []), *coverage.get("notes", [])]
    coverage_gaps = list(coverage.get("notes", [])) if not coverage.get("complete", False) else []
    retrieval_notes = []
    if coverage.get("omitted_files"):
        coverage_gaps.append(f"{len(coverage['omitted_files'])} changed file(s) omitted from review context")
    try:
        # Reserve room for a genuinely separate verification call. No prompts
        # include historical bot comments in evaluation, avoiding answer leakage.
        # Line-number arrays are local validation machinery, not model context;
        # the unified diff already contains line locations. Avoid paying for
        # thousands of redundant integers twice.
        evidence = {**context, "files": [{k:(numbered_patch(v)[0] if k == "patch" and v else v)
                    for k,v in f.items() if k not in ("changed_lines", "right_side_lines")}
                    for f in context["files"]]}
        system = POLICY
        # Protect the required final pass before paying for discovery. Reserve
        # room for discovery's bounded answer as future verification input too;
        # a cache hit may save money but is never assumed in this admission.
        verification_reserve = verifier.required_budget(system, {}, "verification",
            cache_context=evidence, instruction=VERIFY, output_tokens=6000)
        verification_reserve += int(8000 * verifier.prices["input"] * 1.25 * 1.1)
        discovery_ceiling = min(320_000, budget.remaining - verification_reserve)
        if discovery_ceiling <= 0:
            raise BudgetExceeded("Diff cannot fit discovery and verification in the remaining budget; no generation sent")
        discovery = model.invoke(system, {}, budget, "discovery",
                                 ceiling=discovery_ceiling, cache_context=evidence,
                                 instruction=DISCOVERY, effort=discovery_effort)
        stages = [{"phase": "discovery", "result": discovery}]
        proposals = provisional(discovery)
        extra = []
        def decision_payload():
            return {"proposed_findings": proposals, "hypotheses": hypotheses(discovery),
                    "additional_context": compact_context(extra), "open_questions": gaps(discovery),
                    "retrieval_notes": retrieval_notes}

        def admit(items):
            for item in items:
                if item in extra:
                    continue
                if len(json.dumps(compact_context([*extra, item]))) > 65000:
                    retrieval_notes.append("Requested evidence exceeded the review-wide retrieval bound")
                    continue
                extra.append(item)
                try:
                    required = verifier.required_budget(system, decision_payload(), "verification",
                        cache_context=evidence, instruction=VERIFY, output_tokens=6000)
                except BudgetExceeded:
                    extra.pop()
                    retrieval_notes.append("Requested evidence could not fit the verified pricing tier")
                    continue
                except ReviewUnavailable:
                    extra.pop()
                    retrieval_notes.append("Requested evidence could not be admitted because token preflight failed")
                    continue
                if required > budget.remaining:
                    extra.pop()
                    retrieval_notes.append("Requested evidence could not fit the reserved verification budget")

        def stop_on_transport_failure():
            if any(note.startswith("Context transport failure") or "token preflight failed" in note
                   for note in retrieval_notes):
                # Do not pay for investigation/verification on missing required evidence,
                # then discard its findings. Preserve the remaining budget for a retry.
                raise ReviewUnavailable("Temporary context/preflight failure; stopped before further generation")

        first, notes = retrieve(discovery, context, read_file)
        retrieval_notes.extend(notes)
        admit(first)
        stop_on_transport_failure()
        for _ in range(4):
            if not discovery.get("requests"):
                break
            try:
                verification_reserve = verifier.required_budget(system, decision_payload(), "verification",
                    cache_context=evidence, instruction=VERIFY, output_tokens=6000)
            except ReviewUnavailable:
                break  # an optional round cannot prevent the final verification attempt
            # The next 5k-token answer may expand provisional findings. Leave
            # room to pass that answer into verification even on a cache miss.
            investigation_output = 5000
            verification_reserve += int((investigation_output + 1000) * verifier.prices["input"] * 1.25 * 1.1)
            if verification_reserve >= budget.remaining:
                break
            try:
                investigation = verifier.invoke(system, decision_payload(),
                    budget, "investigation", max_output=investigation_output,
                    ceiling=budget.remaining - verification_reserve, cache_context=evidence,
                    instruction=INVESTIGATE)
            except BudgetExceeded:
                # Preflight sent no generation. Preserve the budget for the
                # final decision on the evidence already gathered.
                break
            stages.append({"phase": "investigation", "result": investigation})
            proposals = merge_proposals(proposals, provisional(investigation))
            more, notes = retrieve(investigation, context, read_file)
            discovery = investigation
            retrieval_notes.extend(notes)
            admit(more)
            stop_on_transport_failure()
        final = verifier.invoke(system, decision_payload(),
                             budget, "verification", max_output=6000, cache_context=evidence,
                             instruction=VERIFY, minimum_output=4000)
        findings, invalid = validated_findings(final, context)
        stages.append({"phase": "verification", "result": final})
        # Dropped discovery findings are normal verification, not missing
        # coverage. Invalid final claims, however, cannot become a clean review.
        coverage_gaps.extend(invalid)
        coverage_gaps.extend(gaps(final))
        coverage_gaps.extend(note for note in retrieval_notes if note.startswith(("Context transport failure", "Requested evidence")))
        if hypotheses(final):
            coverage_gaps.append("Verification left unresolved hypotheses")
        # Rejected or empty searches are evidence for the verifier, not proof
        # of missing review coverage. It determines which hypotheses remain.
        if final.get("requests"):
            coverage_gaps.append("Reviewer requested context beyond the bounded verification pass")
        coverage_gaps = list(dict.fromkeys(coverage_gaps))
        complete = bool(coverage.get("complete", False)) and not coverage_gaps
        # A durable partial receipt is final for this exact diff. Transient API
        # failures must remain retryable instead; the publisher keeps their
        # spent budget, emits error and writes no final receipt or findings.
        retryable = any(note.startswith("Context transport failure") or
                        "token preflight failed" in note for note in retrieval_notes)
        status = "unavailable" if retryable else "complete" if complete else "partial"
        return {**base, "status": status, "findings": [] if retryable else findings,
                "retryable": retryable,
                "coverage_gaps": coverage_gaps, "scope_notes": scope_notes, "discovery_findings": len(proposals),
                "retrievals": len(extra),
                "audit": {"proposals": proposals, "additional_context": extra, "stages": stages},
                "retrieval_notes": retrieval_notes,
                "summary": str(final.get("summary", ""))[:500],
                "budget": budget.report(), "elapsed_seconds": round(time.monotonic() - started, 3)}
    except ReviewUnavailable as error:
        return {**base, "status": "unavailable", "findings": [],
                "retryable": any(note.startswith("Context transport failure") or
                                 "token preflight failed" in note for note in retrieval_notes),
                "retrieval_notes": retrieval_notes,
                "coverage_gaps": [*coverage_gaps, str(error)], "budget": budget.report(),
                "elapsed_seconds": round(time.monotonic() - started, 3)}
