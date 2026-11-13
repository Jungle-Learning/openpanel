"""Authenticated incremental scopes. A partial review never advances coverage."""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from review import numbered_patch

CHECKPOINT = re.compile(
    r"<!-- jungle-review:checkpoint v1 head=([0-9a-f]{40}) base=([0-9a-f]{40}) "
    r"ref=([A-Za-z0-9_-]{1,400}) release=([0-9a-f]{64}) run=([1-9][0-9]{0,19}) "
    r"from=([0-9a-f]{40}) sig=([0-9a-f]{64}) -->")
MAX_RELATED_CHARS = 24000


def runtime_identity(directory: Path | None = None, *, model: str = "gpt-6.1-sol") -> str:
    """Hash the trusted reviewer code and evidence snapshots, independent of its checkout commit.

    A new release cannot inherit coverage certified by different review logic.
    Docs/tests do not affect behavior. No repository/PR-supplied release name is trusted.
    """
    directory = directory or Path(__file__).resolve().parent
    files = sorted([p for p in directory.glob("*.py") if not p.name.startswith("test_")]
                   + [p for p in (directory / "docs").glob("*") if p.suffix in (".json", ".txt")])
    hashes = {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    return hashlib.sha256(json.dumps({"source": hashes, "model": model}, sort_keys=True).encode()).hexdigest()


def payload(record: dict) -> str:
    return "|".join(str(record[k]) for k in ("head", "base", "ref", "release", "run", "from"))


def make_checkpoint(repository, number, head, base, base_ref, release, run_id, start, signer):
    encoded = base64.urlsafe_b64encode(base_ref.encode()).decode().rstrip("=")
    record = {"head": head, "base": base, "ref": encoded, "release": release, "run": run_id, "from": start}
    signature = signer.comment_sig(repository, number, "checkpoint", payload(record))
    return (f"<!-- jungle-review:checkpoint v1 head={head} base={base} ref={encoded} "
            f"release={release} run={run_id} from={start} sig={signature} -->")


def parse_checkpoints(body, repository, number, signer):
    records = []
    for match in CHECKPOINT.finditer(body):
        head, base, encoded, release, run, start, signature = match.groups()
        record = {"head": head, "base": base, "ref": encoded, "release": release, "run": int(run), "from": start}
        if not signer.comment_valid(repository, number, "checkpoint", payload(record), signature):
            continue
        try:
            ref = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        except (ValueError, UnicodeError):
            continue
        if not ref or len(ref) > 200:
            continue
        records.append({**record, "base_ref": ref, "marker": match.group(0)})
    return records


def choose_checkpoint(records, *, head, base, base_ref, release, genuine, complete, ancestor):
    """All predicates are required. A read error propagates; it never produces a guessed checkpoint."""
    eligible = []
    for record in records:
        if (record["head"] == head or record["base"] != base or record["base_ref"] != base_ref
                or record["release"] != release):
            continue
        if (genuine(record["run"], record["head"]) and complete(record["head"], record["base"])
                and ancestor(record["head"], head)):
            eligible.append(record)
    # Run IDs are increasing; the additional ancestry test prevents a force-pushed checkpoint.
    # If an older head was rerun later this merely reviews more commits, never skips them.
    return max(eligible, key=lambda r: r["run"]) if eligible else None


def incremental_context(cumulative: dict, delta: dict, checkpoint: dict) -> dict:
    if (cumulative["repository"] != delta["repository"] or cumulative["number"] != delta["number"]
            or cumulative["head_sha"] != delta["head_sha"] or delta["base_sha"] != checkpoint["head"]
            or cumulative["base_sha"] != checkpoint["base"]):
        raise ValueError("Incremental source identities disagree")
    result = deepcopy(delta)
    result["merge_base_sha"] = cumulative["base_sha"]
    result["incremental"] = {
        "mode": "incremental", "from_sha": checkpoint["head"], "to_sha": delta["head_sha"],
        "merge_base_sha": cumulative["base_sha"], "checkpoint_run": checkpoint["run"],
        "instruction": "Only newly introduced/exposed regressions in the delta are review scope. "
                       "Earlier PR changes and unchanged source explain callers, contracts and broader impact."}
    paths = {f["path"] for f in delta["files"]}
    # Show earlier hunks in touched files and their neighboring changes. All other paths remain
    # discoverable, and every source request is pinned to the current head or checkpoint.
    directories = {str(Path(p).parent) for p in paths}
    related, used, omitted = [], 0, []
    for f in sorted(cumulative["files"], key=lambda f: (f["path"] not in paths, f["path"])):
        if f["path"] not in paths and str(Path(f["path"]).parent) not in directories:
            continue
        item = {"path": f["path"], "patch": numbered_patch(f["patch"])[0] if f.get("patch") else None}
        size = len(json.dumps(item, ensure_ascii=False))
        if used + size > MAX_RELATED_CHARS:
            omitted.append(f["path"]); continue
        related.append(item); used += size
    result["cumulative_context"] = {
        "note": "Context only: cumulative PR changes; not the newly changed review surface.",
        "merge_base_sha": cumulative["base_sha"], "head_sha": cumulative["head_sha"],
        "changed_paths": [f["path"] for f in cumulative["files"]],
        "related_changes": related, "related_changes_not_shown": omitted}
    # GitHub accepts inline comments only on the current cumulative PR diff. A delta can revert a file
    # to main, so a delta anchor is not automatically a valid PR anchor.
    result["publishable_lines"] = {f["path"]: sorted(numbered_patch(f.get("patch") or "")[1])
                                   for f in cumulative["files"]}
    return result
