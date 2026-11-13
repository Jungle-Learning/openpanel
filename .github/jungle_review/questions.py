"""Explicit, source-cited resolution of private review questions.

Missing or malformed decisions never clear a coverage gap. This is accounting
for model judgments, not a deterministic proof of source semantics.
"""
import re
from review import CLAIM_FIELDS

QUESTION_INSTRUCTION = """
Verification also returns question_resolutions, one record per question_id in
open_questions. Each record has question_id, verdict (supported/rejected/unresolved),
reason (<=800 characters), evidence (1-8 supplied path:Rline or path:Lline facts for a
resolved question), and finding_index only for supported. Supported must refer to a
surviving verified finding; rejected must cite the source fact that settles the
question without a bug. Unresolved needs the exact missing evidence and may use [].
Do not call missing context a rejection. A hypothetical guard is not a source fact.
These records do not replace dispositions for candidate_id or explicit coverage_gaps.
"""


def question_records(questions):
    return [{"question_id": f"q{i + 1}", "question": question}
            for i, question in enumerate(dict.fromkeys(questions))]


def audit_questions(questions, result, validated, supplied_paths):
    raw = result.get("question_resolutions", []) if result else []
    raw = raw if isinstance(raw, list) else []
    grouped = {}
    for row in raw[:128]:
        if isinstance(row, dict) and isinstance(row.get("question_id"), str):
            grouped.setdefault(row["question_id"], []).append(row)
    locator = re.compile(r"(?<![\w./-])(?:" + "|".join(re.escape(p) for p in sorted(supplied_paths))
                         + r"):[RL][1-9][0-9]*(?!\w)") if supplied_paths else None
    findings = (result or {}).get("findings", [])
    findings = findings if isinstance(findings, list) else []
    gaps, records = [], []
    for question in questions:
        key = question["question_id"]
        rows = grouped.get(key, [])
        problem = "no unique explicit resolution"
        if len(rows) == 1:
            row = rows[0]
            evidence, verdict, reason = row.get("evidence"), row.get("verdict"), row.get("reason")
            shaped = (isinstance(reason, str) and 0 < len(reason.strip()) <= 800
                      and isinstance(evidence, list) and len(evidence) <= 8
                      and all(isinstance(x, str) and 0 < len(x) <= 700 for x in evidence))
            cited = shaped and locator and any(locator.search(x) for x in evidence)
            if verdict == "rejected" and cited and row.get("finding_index") is None:
                problem = None
            elif verdict == "supported" and cited:
                index = row.get("finding_index")
                claim = findings[index] if type(index) is int and 0 <= index < len(findings) else None
                if isinstance(claim, dict) and any(all(claim.get(k) == f.get(k) for k in CLAIM_FIELDS) for f in validated):
                    problem = None
                else:
                    problem = "supported resolution has no surviving verified finding"
            elif verdict == "unresolved" and shaped:
                problem = "unresolved source question"
            else:
                problem = "invalid or unsupported resolution"
            records.append({**question, "resolution": row, "accepted": problem is None})
        else:
            records.append({**question, "accepted": False})
        if problem:
            gaps.append(f"Question {key}: {problem}: {question['question'][:300]}")
    return records, gaps
