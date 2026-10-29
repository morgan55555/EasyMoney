"""Multi-replica end-to-end flow (AGENTS.md §3.2, reqs 7 and 14, scaling).

The strongest check: the *real* services (create group, accept invite, create
expense, leave group) run against the test DB, and their post-commit emissions
route through one replica's ``RedisBus`` onto the shared channel, where *both*
replicas' consumers apply them to their own local registries.

The scenario places a user's socket on replica 0 and another's on replica 1 —
exactly the load-balancer case a single process could never serve. It proves:

  * a REST write handled by replica 0 reaches a socket that lives on replica 1
    (the core horizontal-scaling property);
  * delivery is symmetric: the publisher's own replica also receives, via its
    consumer (not a direct manager call);
  * the join control re-subscribes a joiner's socket on the *other* replica;
  * the leave control stops a leaver's socket on the other replica, so they no
    longer receive group events (including ``member.left`` about themselves).
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.group import Group
from app.models.user import User
from app.schemas.expenses import ExpenseCreate, ParticipantIn
from app.services import expenses as expenses_service
from app.services import groups as groups_service
from app.services import invites as invites_service
from app.ws import hook as ws_hook
from tests import bus_util
from tests.bus_util import Replica

ALICE, BOB = 1, 2


def _expense(payer: int, amount: Decimal, participants: list[int]) -> ExpenseCreate:
    return ExpenseCreate(
        payer_id=payer,
        description=f"paid {amount}",
        amount=amount,
        split_method="equal",
        date=date(2026, 10, 26),
        participants=[ParticipantIn(user_id=p) for p in participants],
    )


def _balances(event: dict[str, Any]) -> dict[int, str]:
    return {row["user_id"]: row["balance"] for row in event["data"]["group_balances"]["balances"]}


async def _run(factory: async_sessionmaker, rep0: Replica, rep1: Replica) -> None:
    async with factory() as db:
        alice = User(id=ALICE, email="a@t.local", password_hash="x", display_name="Alice")
        bob = User(id=BOB, email="b@t.local", password_hash="x", display_name="Bob")
        db.add_all([alice, bob])
        await db.commit()

    # alice's tab lives on replica 0; bob's tab lives on replica 1.
    alice_tab = rep0.connect(ALICE)
    bob_tab = rep1.connect(BOB)

    # --- alice creates the group (handled on replica 0) ----------------------
    async with factory() as db:
        alice = await db.get(User, ALICE)
        assert alice is not None
        group = await groups_service.create_group(db, alice, "Trip", "USD")
    gid = group.id

    # the creator's re-subscribe + member.joined crossed the bus to rep0's own
    # consumer and reached alice's tab there.
    await bus_util.wait_until(lambda: len(alice_tab.of_type("member.joined", gid)) == 1)

    # --- bob joins mid-session via a link invite ------------------------------
    async with factory() as db:
        group = await db.get(Group, gid)
        alice = await db.get(User, ALICE)
        bob = await db.get(User, BOB)
        assert group is not None and alice is not None and bob is not None
        invite = await invites_service.create_invite(db, group, alice, "link")
        await invites_service.accept_invite(db, bob, invite.token)

    # bob's socket is on replica 1: the join control must have re-subscribed it
    # there, and the member.joined group event must have crossed the boundary.
    await bus_util.wait_until(lambda: len(bob_tab.of_type("member.joined", gid)) == 1)
    await bus_util.wait_until(lambda: len(alice_tab.of_type("member.joined", gid)) == 2)

    # --- expense A: alice pays 100, split equal between alice and bob ---------
    async with factory() as db:
        group = await db.get(Group, gid)
        alice = await db.get(User, ALICE)
        assert group is not None and alice is not None
        await expenses_service.create_expense(
            db, group, alice, _expense(ALICE, Decimal("100.00"), [ALICE, BOB])
        )

    # the write was handled on replica 0; the event reached the socket on
    # replica 1 (bob_tab) and the publisher's own socket (alice_tab).
    await bus_util.wait_until(lambda: len(bob_tab.of_type("expense.created", gid)) == 1)
    await bus_util.wait_until(lambda: len(alice_tab.of_type("expense.created", gid)) == 1)

    bob_event = bob_tab.of_type("expense.created", gid)[0]
    alice_event = alice_tab.of_type("expense.created", gid)[0]
    assert _balances(bob_event) == {ALICE: "50.00", BOB: "-50.00"}
    assert _balances(alice_event) == _balances(bob_event)  # single source of truth
    assert sum(Decimal(v) for v in _balances(bob_event).values()) == Decimal(0)

    # --- bob leaves: his replica-1 socket stops receiving group events --------
    async with factory() as db:
        group = await db.get(Group, gid)
        bob = await db.get(User, BOB)
        assert group is not None and bob is not None
        await groups_service.leave_group(db, group, bob)

    # remaining members are told; the leaver's tab must NOT be (it unsubscribed
    # first, before the member.left fan-out). Settle window: if bob's tab were
    # still subscribed, the event would already be there.
    await bus_util.wait_until(lambda: len(alice_tab.of_type("member.left", gid)) == 1)
    await asyncio.sleep(0.2)
    assert bob_tab.of_type("member.left", gid) == []

    # --- expense C: alice only — bob's (unsubscribed) tab gets none of it -----
    async with factory() as db:
        group = await db.get(Group, gid)
        alice = await db.get(User, ALICE)
        assert group is not None and alice is not None
        await expenses_service.create_expense(
            db, group, alice, _expense(ALICE, Decimal("50.00"), [ALICE])
        )

    await bus_util.wait_until(lambda: len(alice_tab.of_type("expense.created", gid)) == 2)
    # bob's tab still holds exactly the one pre-leave expense
    assert len(bob_tab.of_type("expense.created", gid)) == 1


async def test_multi_instance_flow(replicas) -> None:
    rep0, rep1 = replicas
    ws_hook.set_manager(rep0.manager)
    ws_hook.set_bus(rep0.bus)
    try:
        await bus_util.with_test_db(lambda f: _run(f, rep0, rep1))
    finally:
        ws_hook.set_bus(None)
        ws_hook.set_manager(None)
