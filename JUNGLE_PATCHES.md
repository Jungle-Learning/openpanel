# Jungle OpenPanel Patch Notes

This fork carries small, reviewable patches needed for Jungle's self-hosted
analytics setup. Keep changes narrow, document why they exist, and prefer
upstream PRs when a patch is generally useful.

## Active patches

- `session.has_replay` filter: exposes a Sessions-table filter for sessions
  with replay chunks in `session_replay_chunks`, so support/product can find
  replayable sessions without direct ClickHouse queries.

## Patch discipline

- Keep `upstream` pointing at `Openpanel-dev/openpanel`.
- Rebase Jungle branches onto upstream `main` before upgrading production.
- Do not use this fork for broad dashboard redesigns unless the change is
  explicitly tied to Jungle's analytics migration or production reliability.
