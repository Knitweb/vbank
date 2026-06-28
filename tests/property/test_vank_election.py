"""Proofs for signed vank election manifests."""

import pytest

from knitweb.core import canonical, crypto
from knitweb.fabric.web import Web
from knitweb_vank import (
    ELECTION_KIND,
    Election,
    Poll,
    VbankElection,
    VbankPoll,
    collect_elections,
    election_poll_records,
    election_status,
    is_election_open,
)

SCOPE = "vbank"


def _authority():
    priv, _ = crypto.generate_keypair()
    return priv, VbankPoll(priv, SCOPE), VbankElection(priv, SCOPE)


def _poll_records(poll_authority: VbankPoll) -> list[dict]:
    first = poll_authority.define(
        Poll(scope=SCOPE, poll_id="budget", options=3, opens_at=100, closes_at=200)
    )
    second = poll_authority.define(
        Poll(scope=SCOPE, poll_id="board", options=2, opens_at=100, closes_at=200)
    )
    return [first.record, second.record]


@pytest.mark.property
def test_election_manifest_is_signed_and_links_poll_cids():
    _priv, poll_authority, election_authority = _authority()
    polls = _poll_records(poll_authority)
    poll_cids = tuple(canonical.cid(record) for record in polls)
    att = election_authority.define(
        Election(
            scope=SCOPE,
            election_id="municipal-2026",
            title="Municipal 2026",
            poll_cids=poll_cids,
            opens_at=100,
            closes_at=200,
            mode="mixed",
        )
    )

    assert att.verify(author_field="authority")
    assert att.record["kind"] == ELECTION_KIND
    assert att.record["poll_cids"] == list(poll_cids)
    assert att.record["authority"] == election_authority.authority


@pytest.mark.property
def test_collect_elections_and_status():
    _priv, poll_authority, election_authority = _authority()
    polls = _poll_records(poll_authority)
    election = Election(
        scope=SCOPE,
        election_id="municipal-2026",
        title="Municipal 2026",
        poll_cids=tuple(canonical.cid(record) for record in polls),
        opens_at=100,
        closes_at=200,
    )
    web = Web()
    _cid, att = election_authority.weave(election, web)
    web.weave({"kind": "vbank-poll", "scope": SCOPE})  # noise

    assert collect_elections(web) == [att.record]
    assert collect_elections(web, SCOPE) == [att.record]
    assert collect_elections(web, "other") == []
    assert election_status(att.record, 99) == "upcoming"
    assert election_status(att.record, 100) == "open"
    assert is_election_open(att.record, 150)
    assert election_status(att.record, 200) == "closed"


@pytest.mark.property
def test_election_poll_records_resolve_in_manifest_order():
    _priv, poll_authority, election_authority = _authority()
    polls = _poll_records(poll_authority)
    reversed_polls = list(reversed(polls))
    poll_cids = tuple(canonical.cid(record) for record in polls)
    att = election_authority.define(
        Election(
            scope=SCOPE,
            election_id="municipal-2026",
            title="Municipal 2026",
            poll_cids=poll_cids,
            opens_at=100,
            closes_at=200,
        )
    )

    assert election_poll_records(att.record, reversed_polls) == polls
    with pytest.raises(ValueError):
        election_poll_records(att.record, polls[:1])


@pytest.mark.property
@pytest.mark.parametrize(
    "kwargs",
    [
        {"poll_cids": ()},
        {"poll_cids": ("cid-a", "cid-a")},
        {"opens_at": 10, "closes_at": 10},
        {"mode": "unknown"},
    ],
)
def test_invalid_election_definitions_rejected(kwargs):
    base = dict(
        scope=SCOPE,
        election_id="e",
        title="Election",
        poll_cids=("cid-a",),
        opens_at=0,
        closes_at=10,
    )
    base.update(kwargs)
    with pytest.raises((TypeError, ValueError)):
        Election(**base)


@pytest.mark.property
def test_election_authority_scope_must_match():
    priv, _ = crypto.generate_keypair()
    authority = VbankElection(priv, SCOPE)
    with pytest.raises(ValueError):
        authority.define(
            Election(
                scope="other",
                election_id="e",
                title="Election",
                poll_cids=("cid-a",),
                opens_at=0,
                closes_at=10,
            )
        )
