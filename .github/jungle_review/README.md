# Jungle Review

Shared advisory PR reviewer for every Jungle repository and author, using
**GPT-6.1 Sol with medium reasoning**. The same versioned Python bundle is
distributed to each repository, including public repositories. It calls the
provider API directly and has no per-author Cursor or Greptile entitlement.

The November 13 release selects the outcome-focused D14 algorithm after **22
additional configurations and 176 development reviews**. It recovered **4/10
audited bugs**, with **7/7 findings judged valid** and **3/8 reviews complete**,
at **$0.114323 mean / $0.204153 maximum model cost**. These repeatedly exposed
cases do not establish generalization, and automated judgments are fallible.
**It did not meet the 70% recall target.** The owner explicitly authorized shipping
after twenty additional experiments even if that target remained unmet. That is
a release exception, not a passing quality result. On the separate frozen
20-PR diagnostic it emitted six source-checked, agent-judged valid findings but
recovered **0/11 pre-audited roots**; only **2/20 reviews were complete**. Mean
model cost was **$0.161682**, maximum **$0.257496**. This is a limited supplemental
reviewer, not demonstrated parity with either commercial reviewer.

[Research](https://github.com/Jungle-Learning/jungle-web/blob/main/.github/jungle_review/RESEARCH.md),
[evaluation and fresh-case results](https://github.com/Jungle-Learning/jungle-web/blob/main/.github/jungle_review/EVALUATION.md),
and [rollout status](https://github.com/Jungle-Learning/jungle-web/blob/main/docs/pr-review-rollout-2026-11-13.md)
record the evidence and limitations. Findings are advisory; partial coverage
never becomes an authenticated complete-review receipt.

## Behavior

The trusted base-branch workflow reads the immutable PR diff and bounded source
context through GitHub's API. It never checks out or executes candidate code,
installs candidate dependencies, reads repository credentials, or gives a model
shell, network, or write tools. Public forks and bot authors use the same policy.
Drafts wait until ready for review.

Opening a PR reviews its full diff. After a completed review, each push reviews
only changes since that reviewed head, with earlier PR changes and current source
as context for callers, contracts, and wider regressions. A signed checkpoint must
match the PR, base branch, merge base, model and reviewer code; its workflow and
completion receipt are authenticated and its head must be an ancestor of the new
head. Force pushes, base changes and reviewer updates fall back to a full review.
A partial or unavailable review never advances the checkpoint, so its unreviewed
changes stay in scope. Inline findings must also fit the current cumulative PR diff.

The reviewer groups changed files into cohesive units using their import
relationships, then examines ordinary outcomes, boundary contracts and broader
callers. Source is rendered literally to avoid interpreting transport escaping
as code. Bounded follow-up reads investigate specific questions. Every proposal
needs a final verification verdict; unanswered source questions retain a coverage
gap. A second look at audited code is attempted even when discovery finds no
candidate, when it fits the remaining budget.

It posts at most five concise inline comments, one per root cause. Style
preferences, naming, missing tests alone and unrelated pre-existing defects are
excluded. Findings need a trigger, consequence, code evidence, a check for
existing protection and a valid line inside the diff.

The source inventory is bounded at 450,000 characters; admission uses actual
token counts and the remaining budget. Omitted changes and unresolved hypotheses
remain explicit coverage gaps. A larger inventory does not mean that every file
fits into a model call. Later passes receive only relevant hunks, source windows,
and requested callers instead of repeatedly paying for the entire diff.

For dependency contracts, changed API/runtime signals select relevant fixed
documentation snapshots, and the reviewer can request specific IDs. At most two
snapshots from `docs/registry.json`, at most 4,000 characters each. Their source,
retrieval date, and content hash are retained. The runtime verifies the snapshots
locally and never follows a model-supplied URL. Current documentation is evidence
of the documented behavior at its retrieval date, not proof of historical behavior.

Signed open findings from earlier pushes are supplied as untrusted evidence.
When the verifier identifies the same root cause, it can reuse the earlier
identifier to avoid another comment only at the signed finding's current GitHub
line, with the same file and priority. The full bounded prior body is supplied
for that decision. New finding IDs bind the complete claim and commit scope;
a matching title or nearby line alone cannot suppress a different bug. Repeated
identical claims within one response collapse into one finding. A missed semantic
match can still produce a duplicate. The bot never resolves threads automatically.

Each result names the exact reviewed head, model, effort, version, cost and
coverage. Partial or unavailable review is never reported as a clean bill of
health.

**Complete, partial and unavailable (publisher 2026-11-13.4).** A review is
*partial* only when changed production code or configuration was not examined: a
primary unit not audited, a source patch GitHub withheld, unusable model output for
a unit, or suspected defects the final verification could not check. The
reviewer's own open questions, undecided candidates, reads it could not make and
skipped tests, docs or data files are *review notes*: folded into the summary's
Details, never a reason to rerun. On the first production day (November 13) only
2 of 20 reviews were complete; 12 partial results were almost entirely the
reviewer's open questions. Replayed on the 10 newest production scopes, this
release completed 9 and was partial on 1 large PR whose budget ran out. Complete and
partial reviews both leave a green status (advisory); the signed receipt keeps the
outcome, and automated merge paths still accept only `complete`. Only an
*unavailable* review (nothing was reviewed) is red. Reviews are comments, not approvals. Existing tests, security risk gates,
human approval requirements and unresolved review-thread rules still apply.

## Cost contract

**The first attempt has a $0.41 model budget and a $0.09 Actions allowance** within
a $0.50 scope budget, including discovery, verification and retries. Each admitted
retry reserves another $0.09 from that same budget before source or model work;
it reduces the remaining model allowance. These are durable reservations, not a
measurement of the GitHub bill. The cap cannot be raised through workflow inputs
or repository variables.
Before generating, the provider's token-count endpoint sizes the input. The
ledger reserves uncached/cache-write input cost with a counting safety margin,
plus the maximum output cost. Reasoning tokens are output tokens. No generation
starts without enough remaining budget. Discovery also reserves enough for the
mandatory verification pass before it starts.

The scope is repository + PR number + merge base + head. Retargeting a PR creates
a new diff and scope. Provider usage settles each reservation. If a timeout makes
usage unknown, its full reservation stays charged against the cap. Reservations
and signed settlements live in commit status history, which cannot be edited or
deleted. They are restored on retry. Deleting comments or workflow runs cannot
reset the budget. Forged unsigned records can only add spend. A published review
of the same scope is not charged again.
No automatic provider fallback can double the budget. Token rates are pinned
in `provider.py`; an unsupported model fails closed.

Standard USD per million tokens, verified November 12, 2026:

| Model | Input | Cache read | 5/30-minute cache write | Output including reasoning |
| --- | ---: | ---: | ---: | ---: |
| Claude Opus 5.5 | 4 | 0.20 | 5 | 20 |
| GPT-6.1 Sol | 2 | 0.10 | 2.50 | 10 |

The reviewer uses **Sol 6.1 with medium effort for every call**. Two matched
development comparisons favored it over Opus 5.5 medium at this budget. Earlier
high-effort experiments did not improve quality. Every call records its effort. The release exception and remaining quality
limitations are explicit in the evaluation record.
Requests use Flex, with no paid tools, regional premium or long-context tier.
When Flex rejects a request for capacity (uncharged), that call is retried once
on the default tier; the next call tries Flex again. Reservations are already at
standard rates, so this cannot exceed the cap; it only forgoes the Flex discount
for that call. Before this fallback, 4 of the first 20 production reviews were
lost to Flex capacity, and in a replay at 16:00 UTC the previous release was
unavailable on 9 of 9 scopes. Flex also adds about 9 s of latency per call
(trivial request: ~10 s Flex, ~2 s default), which is accepted because the review
runs beside much longer CI jobs.
Flex rates are $1 input, $0.05 cache read, $1.25 for a 30-minute cache write and
$5 output per million tokens. Every call reserves standard rates and settles at
the tier actually served. Unknown billing retains its reservation; an explicit
uncharged Flex resource rejection releases it. Cache writes are disabled unless
an explicit 30-minute source prefix is requested. Unexpected cache lifetime is
accounted at the worst documented rate and stops further work. The token preflight
prevents requests reaching Sol's 272K-input pricing tier.

Actions is a separate compute charge: a standard Linux runner costs $0.006 per
rounded-up minute, before included usage or public-repository allowances. For
example, a three-minute job costs $0.018 and a nine-minute job costs $0.054.
Sol and Actions are additive: a $0.12 model review in a three-minute job totals
about $0.138 before included runner usage. Sol is the reviewer; Actions runs the
workflow that gathers source, calls Sol and publishes its result.
The workflow has a nine-minute job limit and an eight-minute review-step limit;
the $0.09 allowance includes cancellation headroom. Jobs that end before review
admission, including setup failures and already-reviewed reruns, are outside the
scope ledger. Repeated such jobs and provider pricing changes prevent an absolute
GitHub-invoice guarantee. Offline judging is reported separately from review cost.

Across the previous 273 development review attempts, Sol model cost upper bounds
were $0.101016 mean, $0.076289 median and $0.318047 maximum. Ten attempts retain
full unknown-billing reservations. These experiments ran in the sandbox with no
Actions charge; add runner cost when estimating CI usage. That earlier campaign did not qualify a release. The additional November 13
campaign and owner exception are recorded above. Pricing sources:
[Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol),
[Flex](https://developers.openai.com/api/docs/guides/flex-processing), and
[Actions runners](https://docs.github.com/en/billing/reference/actions-runner-pricing).

The current incremental infrastructure passed four private canary runs: initial
review, a comment-only next head, a distinct defect on the following head, and a
same-head rerun. Sol cost $0.011260, $0.007977, $0.010536 and $0 respectively;
each short Actions job cost an estimated $0.006 before included usage. The delta
reviews did not duplicate the earlier open finding, and the rerun made no new
model reservation. These are small synthetic operational checks, not a realistic
PR-cost distribution or review-quality qualification.

On the earlier standard-service, matched eight-PR `schema-v18` comparison, Sol medium
cost $0.232737 median / $0.360777 maximum; Opus medium cost $0.341710 median /
$0.391805 maximum. These figures include incomplete coverage, and the candidate
still missed too many known bugs. The subsequent fresh 12-PR qualification
failed: 87.5% conservative precision and 33.3% validated-root recall, at $0.334093
median / $0.388803 maximum. All 12 reviews were partial. The final runtime was
also rerun on 20 earlier test PRs ($0.2991765 median / $0.406700 maximum); their
independent judgment found 83.3% conservative precision and 25% strict recall.
A live canary also exposed a duplicate on a
line-shifted next head. See the source repository's
[evaluation record](https://github.com/Jungle-Learning/jungle-web/blob/main/.github/jungle_review/EVALUATION.md)
for the qualification decision and later results; development costs alone do
not establish review quality.

## Configuration and operation

- Create the `jungle-review` environment with its branch rule restricted to the
  default branch. Each review records an environment deployment. No approval
  reviewer or wait timer is required.
- Environment secret `JUNGLE_REVIEW_MARKER_KEY`: required, at least 32 random
  characters, unique to this repository. Its HMAC authenticates ledger refunds
  and completion receipts.
- Environment secret `JUNGLE_REVIEW_OPENAI_API_KEY` for the default Sol reviewer.
- Within GitHub, keep these secrets environment-only. The signing key must never
  be copied into repository, organization, or Dependabot secrets. A trusted merge
  service that verifies receipts needs the matching per-repository key in its own
  protected secret store; provision both copies without printing the value.
- Trusted repository variable `JUNGLE_REVIEW_MODEL`: empty or `gpt-6.1-sol`.
  Every other value fails closed. The provider retains Opus only for explicit
  offline comparisons. No author-supplied model or endpoint override is accepted.
- Automatic trigger: opening, updating, reopening, marking a PR ready, or changing
  its base branch. Title and description edits do not spend another review budget.
- Manual trigger: Actions → Jungle Review → Run workflow → PR number, using
  the default branch. This resumes the same head's existing budget.
- Kill switch: disable the Jungle Review workflow in repository Actions settings.
- Rollback: disable the workflow or restore the prior bundle and workflow version.
  Commit status history remains authoritative; comments are display only.

Dependabot and forks use `pull_request_target` from the trusted default branch,
with the same environment and no author filter. Validate environment isolation
and Dependabot secret availability in a live canary before enabling production.

The exact-head `Jungle Review` commit status is the coverage receipt. Automation
must verify its HMAC for the repository, PR, full merge base and full head, as well
as authenticate the linked trusted workflow run. An Actions actor and a link to
a genuine run do not authenticate a status: another workflow can forge that
combination. Reject receipts when the verification key is unavailable. The check
name, signature-shaped text, or generic Actions comment alone is not authority.
Complete results end in a signed `jr1 P` receipt naming the PR, merge-base prefix,
outcome, new findings and still-open findings. Partial results use a success
status whose receipt says `partial`, which automated consumers reject as
incomplete; unavailable results use error and carry no completion receipt. The
workflow itself can finish successfully after reporting those outcomes, so its
conclusion alone is insufficient. The job needs `actions: read` to authenticate
the referenced workflow run.

### Finding markers (v2)

Each inline finding ends with
`<!-- jungle-review:finding v2 fp=<24 hex> head=<sha> base=<merge base> run=<id> path=<base64url> line=<n> text=<sha256> sig=<64 hex> -->`.
The HMAC covers the fingerprint, scope, run, original path, original line, and the
SHA-256 of the comment's rendered text before the marker. A thread counts as an
earlier open finding only when all of these hold:
- it is unresolved and authored by the bot, with no edit by anyone else;
- its text digest, path and original line match the signed values (an outdated
  thread keeps its original path and line, so it still counts);
- its first comment belongs to the earliest valid signed review v2 for the same
  head, merge base and run, so a copied review cannot take the original's place;
- its run is this workflow's run for the PR and head.

Only such threads are deduplicated, counted as still open, or shown to the
reviewer as untrusted evidence. A copied or edited marker therefore makes a
finding post again; it never silences one. A writer can still unresolve a
person's resolved thread, which reopens it as a human choice would.

Compatibility: earlier v1 finding markers
are never trusted. After the upgrade, a still-reachable v1 finding is posted
again once with a v2 marker, and v1 threads stop counting as still open. Review v2
markers and the signed `jr1 P` receipt are unchanged.

### At most one review per scope

A review scope is one repository, PR, merge base and head. The publisher submits at
most one GitHub review per scope; this is not exactly-once, because GitHub's
review API takes no idempotency key. Before each review write it posts a signed
`jr1 W` record. A definite refusal (any 4xx except 408) adds a signed `jr1 X`
record and nothing is published: findings are never moved into an untracked
review body. A timeout, 408 or 5xx is ambiguous: the publisher looks for the
review's signed marker after 2, 5 and 10 seconds and completes if it appears.

Otherwise the scope is held for good: every later run reports `error`
"Publication uncertain, not clean", spends nothing and posts nothing. The hold
ends only when that review's signed, run-verified marker appears, in which case
the run completes from it without reviewing again. A new commit or base change
starts a new scope and is reviewed normally.

There is no time limit and no manual override, so a scope can stay stuck in two
rare cases: the process died between the `W` record and the request, or GitHub
accepted nothing but lost the response. The trade-off is deliberate: a missing
review on one commit is visible and fixed by the next push, while a duplicate
review cannot be withdrawn.

A generic required status is insufficient for automatic merge authorization:
two PRs using the same head can have different merge bases but share a commit's
latest status. Merge automation must verify the exact PR scope and independently
inspect unresolved threads. Temporary retrieval/preflight failures report
unavailable without a completion receipt, allowing a rerun to use the remaining
budget. Once a required retrieval or evidence preflight fails, the reviewer stops
before spending on further investigation or verification. A retry is still
limited by the original scope's remaining budget; an expensive or ambiguous paid
failure can leave too little to finish, requiring a new commit. Evidence that
exceeds a pricing or size bound is deterministic and produces a partial result,
not a transient retry. A genuine bounded partial review remains explicitly partial.

On public repositories, explicitly allow the safe `pull_request_target` workflow
in the repository's Actions event policy, allowing `pull_request_target` and
`workflow_dispatch` only for `.github/workflows/jungle-review.yml`. GitHub's default public-repository
restriction enters enforcement on December 2, 2026. Never opt into checking out
or executing a fork's head in the privileged review job.

## Verification

```sh
python3 -m unittest discover -s .github/jungle_review -p 'test_*.py' -v
```

No packages or provider keys are needed for deterministic tests. They cover
budget reservations, paid incomplete responses, historical SHA isolation, secret
exclusion, line anchors, untrusted input, stale pushes, duplicate publication,
ledger integrity and unavailable-review behavior.

The canonical evaluation harness is `scripts/pr_review_eval.py` in jungle-web.
It replays frozen historical head/base pairs, excludes later PR-body summaries
and bot findings from reviewer inputs, preserves each iteration, and uses an
explicitly selected judge for independent adjudication. Historical bot comments are
reference hypotheses, not unquestionable labels. Report uncertain judgments,
confirmed false positives, missed defects, completion, latency and actual token
cost separately. Annotation-based recall keeps missing and unjudged cases in the
denominator; labels were assembled by agents from source and historical replies,
and disputed labels remain visible. Do not tune prompts on the held-out evaluation set.

## Rollout tooling

The canonical `scripts/install_jungle_review.py` in jungle-web defaults to a dry
run. It reports existing reviewer files, legacy triggers, prior rollout PRs,
environment provisioning and inaccessible settings separately. `--working-tree`
is allowed for dry runs only.

Applying a reviewed release requires its full `--source-commit`, the evaluated
`--expect-release` SHA-256, and concrete `--evidence` (including an explicit owner quality exception when applicable). The
source must be reachable
from `origin/main`, unless an explicit unmerged-source reason is recorded. The
installer opens PRs; it never merges, changes protection, provisions secrets,
force-pushes or modifies legacy reviewers. Existing different files require
inspection and `--replace-existing`. Resuming a branch or reusing a PR verifies
the exact release and absence of unrelated changes. A merged rollout that has
since drifted is reported as blocked.

Provisioning is required before merge. Each run creates a new durable per-repo
record, including failures; an error or blocked repo makes the command fail.
The source repository's `EVALUATION.md` is not copied into target repositories.

## Sources

- [Cursor's review pipeline](https://cursor.com/blog/building-bugbot)
- [Greptile repository context](https://www.greptile.com/docs/how-greptile-works/graph-based-codebase-context)
- [Greptile's noise reduction](https://greptile.com/blog/make-llms-shut-up)
- [Anthropic Code Review](https://code.claude.com/docs/en/code-review)
- [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [Sol 6.1 model and pricing](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
- [OpenAI Flex processing](https://developers.openai.com/api/docs/guides/flex-processing)
- [GitHub Actions runner pricing](https://docs.github.com/en/billing/reference/actions-runner-pricing)
- [GitHub's privileged-workflow guidance](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target)
