# Jungle OpenPanel Patch Notes

This fork carries small, reviewable patches needed for Jungle's self-hosted
analytics setup. Keep changes narrow, document why they exist, and prefer
upstream PRs when a patch is generally useful.

## Active patches

- `session.has_replay` filter: exposes a Sessions-table filter for sessions
  with replay chunks in `session_replay_chunks`, so support/product can find
  replayable sessions without direct ClickHouse queries.
- Calendar-safe chart buckets: date-only ClickHouse buckets are formatted as
  calendar labels instead of UTC instants, preventing August monthly data from
  rendering as July in timezones west of UTC.
- Profile-set formulas and custom events: `UNION(A, B)` and
  `INTERSECTION(A, B)` count ClickHouse profile membership rather than applying
  arithmetic to aggregate values. The report builder provides guided formula
  templates, preserves legacy `A | B` reports as true unions, and can save a
  union/intersection as a reusable query-time custom event.

## Patch discipline

- Keep `upstream` pointing at `Openpanel-dev/openpanel`.
- Rebase Jungle branches onto upstream `main` before upgrading production.
- Do not use this fork for broad dashboard redesigns unless the change is
  explicitly tied to Jungle's analytics migration or production reliability.
