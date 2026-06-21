# vBank Architecture

vBank is a domain package on top of Knitweb/Pulse. It does not define core consensus,
fabric, identity, or personhood primitives. It consumes those primitives to build
auditable voting records.

## Dependency Boundary

Owned here:

- `vbank-ballot`
- `vbank-tally`
- `vbank-poll`
- `vbank-result`
- `vbank-ranked-ballot`
- `vbank-ranked-result`
- `vbank-delegation`
- `vbank-liquid-result`
- `vbank-election`

Provided by Pulse:

- canonical encoding and content IDs;
- signatures and attestations;
- fabric `Web` storage;
- personhood tickets and scope nullifiers.

## Election Manifests

`vbank-election` is a signed manifest for a user-facing election event. It contains:

- `scope`
- `election_id`
- `title`
- `poll_cids`
- `opens_at`
- `closes_at`
- `mode`
- `authority`

The manifest links to signed `vbank-poll` records by CID. Clients can collect
elections first, show their status, then resolve the exact poll records in manifest
order.

## Determinism

All public records are canonical-encoded before signing or returning. Read models sort
by CID when reading from a `Web`. Tally algorithms are order-independent: repeated votes
dedupe by scope nullifier, with highest sequence winning and CID tie-breaks where needed.
