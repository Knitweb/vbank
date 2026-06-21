"""Election manifests for vBank clients and indexers.

A poll is the signed definition for one question. An election is the signed
manifest that groups one or more poll definitions into one user-facing voting
event. The manifest links to poll CIDs, so clients can discover the event first,
then resolve the exact poll records it contains.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

from knitweb.core import canonical, crypto
from knitweb.fabric.attest import Attestation, attest
from knitweb.fabric.web import Web

__all__ = [
    "ELECTION_KIND",
    "Election",
    "VbankElection",
    "collect_elections",
    "election_status",
    "is_election_open",
    "election_poll_records",
]

ELECTION_KIND = "vbank-election"
ELECTION_MODES = {"plurality", "ranked", "liquid", "mixed"}


@dataclass(frozen=True)
class Election:
    """A signed voting event that groups one or more poll definition CIDs."""

    scope: str
    election_id: str
    title: str
    poll_cids: tuple[str, ...]
    opens_at: int
    closes_at: int
    mode: str = "mixed"

    def __post_init__(self) -> None:
        for name in ("scope", "election_id", "title", "mode"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"election {name} must be a non-empty string")
        if self.mode not in ELECTION_MODES:
            raise ValueError(f"unknown election mode: {self.mode!r}")
        for name, value in (("opens_at", self.opens_at), ("closes_at", self.closes_at)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"election {name} must be an int")
        if self.closes_at <= self.opens_at:
            raise ValueError("closes_at must be after opens_at")
        if not isinstance(self.poll_cids, tuple) or not self.poll_cids:
            raise TypeError("poll_cids must be a non-empty tuple")
        if len(set(self.poll_cids)) != len(self.poll_cids):
            raise ValueError("poll_cids must not contain duplicates")
        for cid in self.poll_cids:
            if not isinstance(cid, str) or not cid:
                raise ValueError("each poll_cid must be a non-empty string")


class VbankElection:
    """Election authority: signs election manifests for one scope."""

    def __init__(self, authority_priv: str, scope: str) -> None:
        if not scope:
            raise ValueError("scope must be a non-empty string")
        self._priv = authority_priv
        self.authority_pub = crypto.public_from_private(authority_priv)
        self.authority = crypto.address(self.authority_pub)
        self.scope = scope

    def define(self, election: Election) -> Attestation:
        """Build and sign a ``vbank-election`` manifest record."""
        if election.scope != self.scope:
            raise ValueError(
                f"election scope {election.scope!r} != authority scope {self.scope!r}"
            )
        record = {
            "kind": ELECTION_KIND,
            "scope": election.scope,
            "election_id": election.election_id,
            "title": election.title,
            "poll_cids": list(election.poll_cids),
            "opens_at": election.opens_at,
            "closes_at": election.closes_at,
            "mode": election.mode,
            "authority": self.authority,
        }
        canonical.encode(record)
        return attest(record, self._priv, author_field="authority")

    def weave(self, election: Election, web: Web) -> tuple[str, Attestation]:
        """Sign and weave an election manifest into ``web``."""
        att = self.define(election)
        return web.weave(att.record), att


def collect_elections(web: Web, scope: str | None = None) -> List[dict]:
    """Read all ``vbank-election`` manifests from a woven Web, in CID order."""
    found = [
        record
        for record in web.nodes.values()
        if record.get("kind") == ELECTION_KIND
        and (scope is None or record.get("scope") == scope)
    ]
    found.sort(key=canonical.cid)
    return found


def election_status(election_record: dict, now: int) -> str:
    """Return ``upcoming``, ``open``, or ``closed`` for an election at ``now``."""
    if now < election_record["opens_at"]:
        return "upcoming"
    if now < election_record["closes_at"]:
        return "open"
    return "closed"


def is_election_open(election_record: dict, now: int) -> bool:
    """True iff ``now`` is inside the election window ``[opens_at, closes_at)``."""
    return election_record["opens_at"] <= now < election_record["closes_at"]


def election_poll_records(election_record: dict, poll_records: Iterable[dict]) -> List[dict]:
    """Return the poll records referenced by an election, in manifest order.

    Raises ``ValueError`` if a referenced poll CID is missing. The caller can pass
    records in any order; the manifest order is authoritative for presentation.
    """
    by_cid = {canonical.cid(record): record for record in poll_records}
    out = []
    for cid in election_record["poll_cids"]:
        record = by_cid.get(cid)
        if record is None:
            raise ValueError(f"missing poll record for cid {cid!r}")
        out.append(record)
    return out
