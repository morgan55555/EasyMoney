"""AGENTS.md §3.2, reqs 7 and 14: the real-time layer.

Checkpoint: two sockets of the same user both receive ``expense.created``
(nothing lost, nothing duplicated); a member of another group does not.

The scenario drives the real services against the test DB with fake sockets
registered in a real ``ConnectionManager`` (plugged in through the post-commit
hook), so the full path — commit → hook → fresh on-read snapshot → fan-out —
is exercised without an HTTP server. The live endpoint itself (handshake auth,
ping/pong, drop-on-expiry) is verified in the B8 walkthrough against the
running stack.

Covered:
  * both open tabs of one user receive the same ``expense.created`` event,
    with a correct fresh ``group_balances`` snapshot (sum zero on the wire);
  * REST/socket parity: a fresh REST read of the group balances
    (``load_balances_wire``, what ``GET /groups/{id}/balances`` serves) equals
    the ``group_balances`` wire dict just pushed to every subscribed tab —
    both paths compute on read and converge on one value (reqs 7, 14);
  * the bell: participants get ``notification.created`` (one row per user),
    non-participants do not;
  * deleting an expense bell-notifies its participants with an
    ``expense.deleted`` row (a member who was not a participant gets none);
  * a socket subscribed to a different group receives none of it;
  * dynamic re-subscribe: a user who joins mid-session (link invite accepted)
    starts receiving events on their existing open socket;
  * join bell: existing members are notified of the join, the joiner themself
    is excluded (§2.6);
  * read sync: a mark-read in one tab of a user fans a ``notification.read``
    event to every one of that user's open tabs, and to no other user (req 7).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import joinedload

from app.models.expense import Expense
from app.models.expense_participant import ExpenseParticipant
from app.models.group import Group
from app.models.membership import Membership
from app.models.notification import Notification
from app.models.user import User
from app.schemas.expenses import ExpenseCreate, ParticipantIn
from app.services import balances as balances_service
from app.services import expenses as expenses_service
from app.services import groups as groups_service
from app.services import invites as invites_service
from app.services import notifications as notifications_service
from app.ws import hook as ws_hook
from app.ws.registry import ConnectionManager

ALICE, BOB, CAROL, DAVE = 1, 2, 3, 4
GROUP_ID = 1


class FakeSocket:
    """Duck-types a Starlette WebSocket for the registry's send path."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


def _events(sock: FakeSocket) -> list[dict[str, Any]]:
    return [json.loads(text) for text in sock.sent]


def _of_type(
    sock: FakeSocket, event_type: str, group_id: int | None = None
) -> list[dict[str, Any]]:
    events = [e for e in _events(sock) if e["type"] == event_type]
    if group_id is not None:
        events = [e for e in events if e["group_id"] == group_id]
    return events


def _balances(event: dict[str, Any]) -> dict[int, str]:
    return {row["user_id"]: row["balance"] for row in event["data"]["group_balances"]["balances"]}


async def _scenario(factory: async_sessionmaker) -> None:
    manager = ConnectionManager()
    ws_hook.set_manager(manager)
    try:
        await _run(factory, manager)
    finally:
        ws_hook.set_manager(None)


async def _run(factory: async_sessionmaker, manager: ConnectionManager) -> None:
    # --- actors ---------------------------------------------------------------
    async with factory() as db:
        for uid in (ALICE, BOB, CAROL, DAVE):
            db.add(
                User(
                    id=uid,
                    email=f"u{uid}@test.local",
                    password_hash="not-a-real-hash",
                    display_name=f"User {uid}",
                )
            )
        await db.commit()

    # Alice's two tabs are connected before the group exists: no groups yet.
    alice_tab1, alice_tab2 = FakeSocket(), FakeSocket()
    manager.connect(alice_tab1, ALICE)
    manager.connect(alice_tab2, ALICE)

    # --- group 1 (EUR): alice creates it, bob joins ---------------------------
    async with factory() as db:
        alice = await db.get(User, ALICE)
        assert alice is not None
        group = await groups_service.create_group(db, alice, "Trip", "EUR")
        db.add(Membership(group_id=group.id, user_id=BOB))
        await db.commit()
        group_id = group.id

    # The creator's re-subscribe: both open tabs immediately see member.joined.
    for tab in (alice_tab1, alice_tab2):
        joined = _of_type(tab, "member.joined", group_id)
        assert len(joined) == 1
        assert joined[0]["data"]["user_id"] == ALICE

    bob_tab = FakeSocket()
    manager.connect(bob_tab, BOB)
    manager.subscribe_group(bob_tab, group_id)  # a tab opened after bob joined

    # --- group 2 (USD): carol, a member of a different group -------------------
    async with factory() as db:
        carol = await db.get(User, CAROL)
        assert carol is not None
        other = await groups_service.create_group(db, carol, "Other", "USD")
    carol_tab = FakeSocket()
    manager.connect(carol_tab, CAROL)
    manager.subscribe_group(carol_tab, other.id)

    # dave is in no group yet; his tab stays open through the join below.
    dave_tab = FakeSocket()
    manager.connect(dave_tab, DAVE)

    # --- expense 1: alice pays 100.00, split equal between alice and bob -------
    async with factory() as db:
        group = await db.get(Group, group_id)
        alice = await db.get(User, ALICE)
        assert group is not None and alice is not None
        await expenses_service.create_expense(
            db,
            group,
            alice,
            ExpenseCreate(
                payer_id=ALICE,
                description="dinner",
                amount=Decimal("100.00"),
                split_method="equal",
                date=date(2026, 10, 26),
                participants=[ParticipantIn(user_id=ALICE), ParticipantIn(user_id=BOB)],
            ),
        )

    # Checkpoint: both of alice's tabs got the event, and so did bob's.
    for sock in (alice_tab1, alice_tab2, bob_tab):
        created = _of_type(sock, "expense.created", group_id)
        assert len(created) == 1
        data = created[0]["data"]
        assert data["amount"] == "100.00"
        assert data["currency"] == "EUR"
        assert {p["user_id"]: p["share"] for p in data["participants"]} == {
            ALICE: "50.00",
            BOB: "50.00",
        }
        balances = _balances(created[0])
        assert balances == {ALICE: "50.00", BOB: "-50.00"}
        assert sum(Decimal(v) for v in balances.values()) == Decimal(0)
        gb = created[0]["data"]["group_balances"]
        assert gb["pairs"] == [{"from_user_id": BOB, "to_user_id": ALICE, "amount": "50.00"}]
        assert gb["transfers"] == [{"from_user_id": BOB, "to_user_id": ALICE, "amount": "50.00"}]

    # The bell: exactly one expense.created notification per participant tab,
    # one row per user (the ids differ). Type-filtered so the later
    # member.joined bell of this scenario cannot leak into this count.
    expense_notes = {
        sock: [
            e["data"]
            for e in _of_type(sock, "notification.created", group_id)
            if e["data"]["type"] == "expense.created"
        ]
        for sock in (alice_tab1, alice_tab2, bob_tab)
    }
    alice_note = expense_notes[alice_tab1][0]
    bob_note = expense_notes[bob_tab][0]
    assert [len(v) for v in expense_notes.values()] == [1, 1, 1]
    assert alice_note["id"] != bob_note["id"]
    assert alice_note["payload"] == bob_note["payload"]

    # The other-group member and the non-member got nothing.
    assert carol_tab.sent == []
    assert dave_tab.sent == []

    # --- dave joins group 1 mid-session via a link invite -----------------------
    async with factory() as db:
        group = await db.get(Group, group_id)
        bob = await db.get(User, BOB)
        dave = await db.get(User, DAVE)
        assert group is not None and bob is not None and dave is not None
        invite = await invites_service.create_invite(db, group, bob, "link")
        joined_group = await invites_service.accept_invite(db, dave, invite.token)
    assert joined_group.id == group_id

    # dave's existing tab — open since before the join — now receives the
    # group event, and the existing members are bell-notified (§2.6).
    dave_joined = _of_type(dave_tab, "member.joined", group_id)
    assert len(dave_joined) == 1
    assert dave_joined[0]["data"]["user_id"] == DAVE
    for sock in (alice_tab1, alice_tab2, bob_tab):
        join_notes = [
            e["data"]
            for e in _of_type(sock, "notification.created", group_id)
            if e["data"]["type"] == "member.joined"
        ]
        assert len(join_notes) == 1
        assert join_notes[0]["payload"]["user_id"] == DAVE

    # --- expense 2: bob pays 20.00, split equal between alice and bob -----------
    async with factory() as db:
        group = await db.get(Group, group_id)
        bob = await db.get(User, BOB)
        assert group is not None and bob is not None
        await expenses_service.create_expense(
            db,
            group,
            bob,
            ExpenseCreate(
                payer_id=BOB,
                description="drinks",
                amount=Decimal("20.00"),
                split_method="equal",
                date=date(2026, 10, 26),
                participants=[ParticipantIn(user_id=ALICE), ParticipantIn(user_id=BOB)],
            ),
        )

    # Everyone in group 1 who was a member when expense 1 was created holds
    # both events; the second carries the fresh snapshot (dave, a member
    # without expenses, appears at 0.00).
    for sock in (alice_tab1, alice_tab2, bob_tab):
        created = _of_type(sock, "expense.created", group_id)
        assert len(created) == 2
        assert created[1]["data"]["amount"] == "20.00"
        balances = _balances(created[1])
        assert balances == {ALICE: "40.00", BOB: "-40.00", DAVE: "0.00"}
        assert sum(Decimal(v) for v in balances.values()) == Decimal(0)
        gb = created[1]["data"]["group_balances"]
        assert gb["transfers"] == [{"from_user_id": BOB, "to_user_id": ALICE, "amount": "40.00"}]

    # dave joined after expense 1, so his re-subscribed tab got only expense 2
    # — but its snapshot already carries his 0.00 row (he is a member).
    dave_created = _of_type(dave_tab, "expense.created", group_id)
    assert len(dave_created) == 1
    assert dave_created[0]["data"]["amount"] == "20.00"
    balances = _balances(dave_created[0])
    assert balances == {ALICE: "40.00", BOB: "-40.00", DAVE: "0.00"}
    assert sum(Decimal(v) for v in balances.values()) == Decimal(0)

    # REST/socket parity (reqs 7, 14): a fresh REST read of the same group —
    # what `GET /groups/{id}/balances` serves — must equal the
    # `group_balances` wire dict just pushed to every subscribed tab. Balances
    # are computed on read (invariant 4), so both paths converge on one value;
    # no state has changed between the push and this read.
    async with factory() as db:
        group = await db.get(Group, group_id)
        assert group is not None
        rest = await balances_service.load_balances_wire(db, group)
    assert rest["balances"] == [
        {"user_id": ALICE, "display_name": "User 1", "balance": "40.00", "avatar_url": None},
        {"user_id": BOB, "display_name": "User 2", "balance": "-40.00", "avatar_url": None},
        {"user_id": DAVE, "display_name": "User 4", "balance": "0.00", "avatar_url": None},
    ]
    for sock in (alice_tab1, alice_tab2, bob_tab, dave_tab):
        pushed = _of_type(sock, "expense.created", group_id)[-1]["data"]["group_balances"]
        assert pushed == rest

    # dave was a member but not a participant: no expense bell; and §2.6
    # excludes the joiner from the member.joined fan-out, so his tab holds no
    # bell events at all (the group event above is not a bell).
    assert _of_type(dave_tab, "notification.created", group_id) == []

    # The other-group member still got none of it.
    assert carol_tab.sent == []

    # --- mark-read fans out to every open tab of the user (req 7) ---------------
    # Alice's bell holds the expense + member.joined rows from above (all unread).
    # She marks them read from one tab; both of her tabs must learn of the
    # read-state change via the user-scoped `notification.read` event, while no
    # other user's socket does.
    async with factory() as db:
        alice = await db.get(User, ALICE)
        assert alice is not None
        marked = await notifications_service.mark_read(db, alice, None)
        assert marked >= 1
    for sock in (alice_tab1, alice_tab2):
        assert len(_of_type(sock, "notification.read")) == 1
    for sock in (bob_tab, carol_tab, dave_tab):
        assert _of_type(sock, "notification.read") == []


async def _delete_notifies_participants_scenario(factory: async_sessionmaker) -> None:
    """§2.6: deleting an expense bell-notifies its participants (payer ∪
    participants; the deleter included when involved) with an
    ``expense.deleted`` row, and no one else. The post-commit
    ``notification.created`` fan-out carries the deletion payload; the rows
    persist for the bell's REST read."""
    manager = ConnectionManager()
    ws_hook.set_manager(manager)
    try:
        # alice (creator + payer), bob (participant), carol (member, but not a
        # participant of the expense that gets deleted).
        async with factory() as db:
            for uid in (ALICE, BOB, CAROL):
                db.add(
                    User(
                        id=uid,
                        email=f"u{uid}@test.local",
                        password_hash="not-a-real-hash",
                        display_name=f"User {uid}",
                    )
                )
            await db.commit()

        alice_tab = FakeSocket()
        bob_tab = FakeSocket()
        carol_tab = FakeSocket()
        manager.connect(alice_tab, ALICE)
        manager.connect(bob_tab, BOB)
        manager.connect(carol_tab, CAROL)

        async with factory() as db:
            alice = await db.get(User, ALICE)
            assert alice is not None
            group = await groups_service.create_group(db, alice, "Trip", "EUR")
            for uid in (BOB, CAROL):
                db.add(Membership(group_id=group.id, user_id=uid))
            await db.commit()
            group_id = group.id
        manager.subscribe_group(bob_tab, group_id)
        manager.subscribe_group(carol_tab, group_id)

        # Expense paid by alice, split between alice and bob; carol is left out.
        async with factory() as db:
            group = await db.get(Group, group_id)
            alice = await db.get(User, ALICE)
            assert group is not None and alice is not None
            expense = await expenses_service.create_expense(
                db,
                group,
                alice,
                ExpenseCreate(
                    payer_id=ALICE,
                    description="dinner",
                    amount=Decimal("30.00"),
                    split_method="equal",
                    date=date(2026, 10, 26),
                    participants=[ParticipantIn(user_id=ALICE), ParticipantIn(user_id=BOB)],
                ),
            )
            expense_id = expense.id

        # Delete through the service, loading the expense the way the router
        # does (group + participants eager) so the participant set is present.
        async with factory() as db:
            alice = await db.get(User, ALICE)
            assert alice is not None
            expense = (
                await db.execute(
                    select(Expense)
                    .options(
                        joinedload(Expense.group),
                        joinedload(Expense.payer),
                        joinedload(Expense.participants).joinedload(ExpenseParticipant.user),
                        joinedload(Expense.attachment),
                    )
                    .where(Expense.id == expense_id)
                )
            ).scalars().unique().one()
            await expenses_service.delete_expense(db, expense, alice)

        # Both participants' tabs learn of the deletion through the bell ...
        for sock in (alice_tab, bob_tab):
            notes = [
                e["data"]
                for e in _of_type(sock, "notification.created", group_id)
                if e["data"]["type"] == "expense.deleted"
            ]
            assert len(notes) == 1
            assert notes[0]["payload"]["description"] == "dinner"
            # The payload carries group_id so the View button can deep-link
            # into the group's activity tab (§2.14).
            assert notes[0]["payload"]["group_id"] == group_id
        # ... and no one else: carol was a member but not a participant.
        assert _of_type(carol_tab, "notification.created", group_id) == []

        # The rows persist (one per participant) for the bell's REST read.
        async with factory() as db:
            rows = (
                await db.execute(
                    select(Notification.user_id)
                    .where(Notification.type == "expense.deleted")
                    .order_by(Notification.user_id)
                )
            ).all()
            assert [uid for (uid,) in rows] == [ALICE, BOB]
    finally:
        ws_hook.set_manager(None)


def test_ws_multiuser(run_db) -> None:
    run_db(_scenario)


def test_delete_notifies_participants(run_db) -> None:
    run_db(_delete_notifies_participants_scenario)
