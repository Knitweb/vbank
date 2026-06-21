# Knitweb vBank

Standalone vBank domain package for Knitweb/Pulse.

This repo owns the voting-domain layer:

- personhood-gated ballot emission;
- deterministic one-person-one-vote tallying;
- signed poll definitions and independently auditable results;
- weighted, liquid, and ranked-choice voting;
- signed election manifests that group multiple poll definitions for clients and indexers.

Pulse remains the dependency for core primitives: canonical encoding/CIDs, signatures,
fabric Web storage, attestations, and personhood tickets.

## Layout

- `src/knitweb_vbank/` - package code.
- `tests/property/` - deterministic regression/property tests.
- `docs/ARCHITECTURE.md` - package boundary and record overview.

## Development

Until Pulse is published as a package, point `PYTHONPATH` at this repo and a Pulse checkout:

```bash
PYTHONPATH=src:/private/tmp/pulse-pr187/src python -m pytest -q
```

If you have a fresher Pulse checkout, use its `src` path instead.

## New in this repo

The `vbank-election` manifest is the first layer that belongs naturally outside the
Pulse core. It signs a user-facing election event that links to one or more signed
`vbank-poll` definitions by CID, giving frontends and indexers a stable object to
discover before resolving the individual poll records.
