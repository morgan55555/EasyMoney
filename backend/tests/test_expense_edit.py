"""Expense-edit API regressions (AGENTS.md §2.5).

P1: a partial edit that omits ``amount`` must keep the stored value. Money
columns are ``NUMERIC(14,3)`` (§3.7), so a 2-digit-currency amount read back
from the database carries scale 3 (``5.000``); the re-split path's strict
sub-precision check — which correctly rejects *user input* finer than the
minor unit (``1.999`` → 422, see ``test_currency.py``) — must not fire on
the canonical stored value.
"""

from __future__ import annotations

from app.core import security
from app.models.membership import Membership
from app.models.user import User
from app.services import groups as groups_service


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_eur_group(run_db) -> str:
    """Two users in one EUR group; returns user 1's access token."""

    async def _scenario(factory) -> None:
        async with factory() as db:
            db.add(User(id=1, email="a@test.local", password_hash="x", display_name="A"))
            db.add(User(id=2, email="b@test.local", password_hash="x", display_name="B"))
            await db.commit()
            user = await db.get(User, 1)
            assert user is not None
            await groups_service.create_group(db, user, "G-EUR", "EUR")
            db.add(Membership(group_id=1, user_id=2))
            await db.commit()

    run_db(_scenario)
    return security.create_access_token(1)


def _seed_jpy_group(run_db) -> str:
    async def _scenario(factory) -> None:
        async with factory() as db:
            db.add(User(id=1, email="a@test.local", password_hash="x", display_name="A"))
            await db.commit()
            user = await db.get(User, 1)
            assert user is not None
            await groups_service.create_group(db, user, "G-JPY", "JPY")
            await db.commit()

    run_db(_scenario)
    return security.create_access_token(1)


class TestPartialEditKeepsStoredAmount:
    """Omitting ``amount`` on a PATCH must not re-validate the DB-scaled value."""

    def test_description_only_edit_survives_the_db_scale(self, run_db, api_client) -> None:
        token = _seed_eur_group(run_db)
        create = api_client.post(
            "/api/groups/1/expenses",
            headers=_auth(token),
            json={
                "payer_id": 1,
                "description": "original",
                "amount": "5.00",
                "split_method": "equal",
                "date": "2026-01-01",
                "participants": [{"user_id": 1}, {"user_id": 2}],
            },
        )
        assert create.status_code == 201, create.text
        expense = create.json()

        # The stored amount is NUMERIC(14,3) → Decimal("5.000") in the DB.
        # A partial edit omitting the amount must succeed (P1: it was 422).
        edit = api_client.patch(
            f"/api/expenses/{expense['id']}",
            headers=_auth(token),
            json={"description": "edited"},
        )
        assert edit.status_code == 200, edit.text
        updated = edit.json()
        assert updated["description"] == "edited"
        assert updated["amount"] == "5.00"
        assert [p["share"] for p in updated["participants"]] == ["2.50", "2.50"]

    def test_comment_only_edit_on_zero_digit_currency(self, run_db, api_client) -> None:
        token = _seed_jpy_group(run_db)
        create = api_client.post(
            "/api/groups/1/expenses",
            headers=_auth(token),
            json={
                "payer_id": 1,
                "description": "tokyo",
                "amount": "101",
                "split_method": "equal",
                "date": "2026-01-01",
                "participants": [{"user_id": 1}],
            },
        )
        assert create.status_code == 201, create.text
        expense = create.json()
        assert expense["amount"] == "101"

        edit = api_client.patch(
            f"/api/expenses/{expense['id']}",
            headers=_auth(token),
            json={"comment": "edited"},
        )
        assert edit.status_code == 200, edit.text
        updated = edit.json()
        assert updated["comment"] == "edited"
        assert updated["amount"] == "101"

    def test_explicit_amount_still_validated(self, run_db, api_client) -> None:
        """The precision guard stays strict for user-supplied amounts."""
        token = _seed_eur_group(run_db)
        create = api_client.post(
            "/api/groups/1/expenses",
            headers=_auth(token),
            json={
                "payer_id": 1,
                "description": "original",
                "amount": "5.00",
                "split_method": "equal",
                "date": "2026-01-01",
                "participants": [{"user_id": 1}],
            },
        )
        assert create.status_code == 201, create.text
        expense = create.json()

        bad = api_client.patch(
            f"/api/expenses/{expense['id']}",
            headers=_auth(token),
            json={"amount": "1.999"},
        )
        assert bad.status_code == 422
