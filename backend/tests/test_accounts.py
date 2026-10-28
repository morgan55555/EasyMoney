"""AGENTS.md §5.6 / §2.13: accounts, verification, admin and mail controls.

Every scenario runs against the test DB (fresh, truncated schema per scenario)
and drives the real services — not HTTP. Domain errors are asserted directly
(``Forbidden`` == 403, ``Conflict`` == 409, ``NotFound`` == 404,
``ValidationFailed`` == 422); the API layer is a thin mapper over them.

Settings are controlled per-test through environment variables + an
``lru_cache`` clear (the same mechanism the boot-time config uses), so each
scenario sees exactly the toggles it needs:

  * first-ever user becomes admin; the migration backfill promotes the
    lowest-id user when an install has users but no admin;
  * registration restriction (``REGISTRATION_OPEN=false`` -> 403 once a user
    exists, and ``get_registration_status`` reflects it);
  * register-confirmation on/off (unverified-until-confirmed vs. verified
    immediately, with/without a mailed token);
  * email-change confirmation on/off (token to the previous address vs. an
    immediate swap) plus its 409s;
  * token lifecycle (confirm marks used; a second confirm 409s; resend revokes
    the previous pending token; an expired token 409s);
  * password-change confirmation on/off (the pending new-password hash rides
    on the token and is applied only on confirmation; too-short 422s, the
    current password 409s; a reissue revokes the previous token);
  * register with ``preferred_language`` (stored, defaults to "en", an
    invalid value 422s);
  * the SMTP kill switch (``SMTP_ENABLED=false`` makes ``send_mail`` a no-op
    even with ``SMTP_HOST`` set, and skips confirmations);
  * avatar set/clear (base64 round-trip, content-type allow-list, size cap);
  * admin create/patch (no confirmation, duplicate-email 409, the
    last-admin lockout guard) and the statistics aggregate.

``SMTP_HOST`` is left unset throughout, so the real ``send_mail`` is log-only;
where a mail must be *observed*, ``app.services.mail.send_mail`` is swapped for
a recorder (the same pattern as ``test_reminders``).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.security import hash_password, verify_password
from app.models.email_token import (
    PURPOSE_CHANGE,
    PURPOSE_PASSWORD,
    PURPOSE_REGISTER,
    PURPOSE_REVERT,
    STATUS_PENDING,
    STATUS_REVOKED,
    STATUS_USED,
    EmailToken,
)
from app.models.expense import Expense
from app.models.expense_participant import ExpenseParticipant
from app.models.group import Group
from app.models.membership import Membership
from app.models.settlement import Settlement
from app.models.user import User
from app.services import admin as admin_service
from app.services import auth as auth_service
from app.services import avatar as avatar_service
from app.services import mail as mail_service

# Money values used by the statistics scenario.
DECIMAL_10 = Decimal("10.00")
DECIMAL_4 = Decimal("4.00")


# --- settings control --------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Any:
    """``get_settings`` is ``lru_cache``d; a test that flips env vars must not
    leak its Settings object into the next test. Clear after every test (the
    env is restored by ``monkeypatch`` before this finalizer runs)."""
    yield
    get_settings.cache_clear()


def _env(monkeypatch: pytest.MonkeyPatch, **kv: object) -> None:
    """Apply settings overrides via the environment, then drop the cached
    Settings so the next service call rebuilds from the patched env."""
    for key, value in kv.items():
        monkeypatch.setenv(key, str(value))
    get_settings.cache_clear()


def _recorder() -> tuple[Any, list[tuple[str, str, str, str | None]]]:
    """A stand-in for ``send_mail`` that records (to, subject, text, html)."""
    sent: list[tuple[str, str, str, str | None]] = []

    async def send(to: str, subject: str, text: str, html: str | None = None) -> None:
        sent.append((to, subject, text, html))

    return send, sent


# --- DB helpers --------------------------------------------------------------


async def _mk_user(
    factory: async_sessionmaker,
    uid: int,
    *,
    email: str | None = None,
    password: str = "password1",
    is_admin: bool = False,
    email_verified: bool = True,
    display_name: str | None = None,
) -> User:
    async with factory() as db:
        user = User(
            id=uid,
            email=email or f"u{uid}@test.local",
            password_hash=hash_password(password),
            display_name=display_name or f"User {uid}",
            is_admin=is_admin,
            email_verified=email_verified,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


async def _get_user(factory: async_sessionmaker, uid: int) -> User:
    async with factory() as db:
        user = await db.get(User, uid)
        assert user is not None
        return user


async def _tokens_for(
    factory: async_sessionmaker, uid: int, purpose: str
) -> list[EmailToken]:
    async with factory() as db:
        rows = (
            await db.execute(
                select(EmailToken)
                .where(EmailToken.user_id == uid, EmailToken.purpose == purpose)
                .order_by(EmailToken.id)
            )
        ).scalars().all()
        return list(rows)


# --- 1. first user is admin + migration backfill ----------------------------


async def _first_ever_user_is_admin(factory: async_sessionmaker) -> None:
    # Fresh (truncated) DB: the first registrant becomes the admin.
    async with factory() as db:
        first_id = (
            await auth_service.register(db, "first@test.local", "password1", None)
        ).id
    assert (await _get_user(factory, first_id)).is_admin is True
    async with factory() as db:
        second_id = (
            await auth_service.register(db, "second@test.local", "password1", None)
        ).id
    assert (await _get_user(factory, second_id)).is_admin is False


async def _migration_backfills_lowest_id_admin(factory: async_sessionmaker) -> None:
    """The a3f9c2e6b7d1 migration promotes the lowest-id user when an install
    has users but no admin. Reproduce its exact statements here so the
    backfill semantics are pinned without re-running alembic."""
    await _mk_user(factory, 3, email="c@test.local")
    await _mk_user(factory, 1, email="a@test.local")
    await _mk_user(factory, 2, email="b@test.local")
    async with factory() as db:
        has_admin = (
            await db.execute(text("SELECT 1 FROM users WHERE is_admin = true LIMIT 1"))
        ).first()
        assert has_admin is None
        min_id = (
            await db.execute(text("SELECT id FROM users ORDER BY id ASC LIMIT 1"))
        ).scalar()
        assert min_id == 1
        await db.execute(
            text("UPDATE users SET is_admin = true WHERE id = :id"), {"id": min_id}
        )
        await db.commit()
    # Only the lowest-id user was promoted.
    assert (await _get_user(factory, 1)).is_admin is True
    assert (await _get_user(factory, 2)).is_admin is False
    assert (await _get_user(factory, 3)).is_admin is False


# --- 2. registration restriction --------------------------------------------


async def _registration_closed_after_first_user(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, REGISTRATION_OPEN="false")
    # While the users table is empty, the first user may always register.
    async with factory() as db:
        first_id = (
            await auth_service.register(db, "first@test.local", "password1", None)
        ).id
    assert (await _get_user(factory, first_id)).is_admin is True
    async with factory() as db:
        assert await auth_service.get_registration_status(db) is False
    # Now that a user exists, registration is closed (403) ...
    with pytest.raises(Forbidden):
        async with factory() as db:
            await auth_service.register(db, "second@test.local", "password1", None)
    # ... and the status endpoint agrees the form should be hidden.
    async with factory() as db:
        assert await auth_service.get_registration_status(db) is False


async def _registration_open_ignores_user_count(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, REGISTRATION_OPEN="true")
    async with factory() as db:
        await auth_service.register(db, "first@test.local", "password1", None)
        await auth_service.register(db, "second@test.local", "password1", None)
    # Open: further users may register and the form stays visible.
    async with factory() as db:
        assert await auth_service.get_registration_status(db) is True


async def _registration_status_true_on_empty_db(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, REGISTRATION_OPEN="false")
    # Closed, but no user exists yet -> still open for the very first account.
    async with factory() as db:
        assert await auth_service.get_registration_status(db) is True


# --- 3. register-confirmation on/off ----------------------------------------


async def _register_confirmation_active(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    send, sent = _recorder()
    monkeypatch.setattr(mail_service, "send_mail", send)
    _env(
        monkeypatch,
        SMTP_ENABLED="true",
        EMAIL_REGISTER_CONFIRMATION="true",
        EMAIL_CHANGE_CONFIRMATION="false",
    )
    async with factory() as db:
        user_id = (
            await auth_service.register(db, "alice@test.local", "password1", "Alice")
        ).id
    # Unverified until confirmed, and a pending register token was issued ...
    assert (await _get_user(factory, user_id)).email_verified is False
    tokens = await _tokens_for(factory, user_id, PURPOSE_REGISTER)
    assert len(tokens) == 1
    assert tokens[0].status == STATUS_PENDING
    assert tokens[0].target_email == "alice@test.local"
    # ... and the confirmation mail went to the user's address.
    assert len(sent) == 1
    assert sent[0][0] == "alice@test.local"

    # Confirming the token verifies the address and marks the token used.
    async with factory() as db:
        confirmed = await auth_service.confirm_email_token(db, tokens[0].token)
        assert confirmed.email_verified is True
    assert (await _get_user(factory, user_id)).email_verified is True
    assert (await _tokens_for(factory, user_id, PURPOSE_REGISTER))[0].status == STATUS_USED


async def _register_confirmation_inactive(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    send, sent = _recorder()
    monkeypatch.setattr(mail_service, "send_mail", send)
    _env(monkeypatch, SMTP_ENABLED="true", EMAIL_REGISTER_CONFIRMATION="false")
    async with factory() as db:
        user_id = (await auth_service.register(db, "bob@test.local", "password1", None)).id
    # Verified immediately: no token, no mail.
    assert (await _get_user(factory, user_id)).email_verified is True
    assert await _tokens_for(factory, user_id, PURPOSE_REGISTER) == []
    assert sent == []


# --- 4. email-change confirmation on/off ------------------------------------


async def _email_change_confirmation_active(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    send, sent = _recorder()
    monkeypatch.setattr(mail_service, "send_mail", send)
    _env(
        monkeypatch,
        SMTP_ENABLED="true",
        EMAIL_CHANGE_CONFIRMATION="true",
        EMAIL_REGISTER_CONFIRMATION="false",
    )
    await _mk_user(factory, 1, email="old@test.local")
    async with factory() as db:
        user = await db.get(User, 1)
        assert user is not None
        status = await auth_service.change_email(db, user, "new@test.local")
    assert status == "confirmation_sent"
    # The swap is NOT applied yet. Two tokens are issued: a pending "change"
    # token targeting the NEW email and a pending "revert" token targeting the
    # PREVIOUS email, and one mail goes to each address.
    assert (await _get_user(factory, 1)).email == "old@test.local"
    change_toks = await _tokens_for(factory, 1, PURPOSE_CHANGE)
    revert_toks = await _tokens_for(factory, 1, PURPOSE_REVERT)
    assert len(change_toks) == 1
    assert change_toks[0].status == STATUS_PENDING
    assert change_toks[0].target_email == "new@test.local"
    assert len(revert_toks) == 1
    assert revert_toks[0].status == STATUS_PENDING
    assert revert_toks[0].target_email == "old@test.local"
    assert len(sent) == 2
    assert {m[0] for m in sent} == {"old@test.local", "new@test.local"}

    # Confirming applies the swap and marks the change token used ...
    async with factory() as db:
        await auth_service.confirm_email_token(db, change_toks[0].token)
    assert (await _get_user(factory, 1)).email == "new@test.local"
    assert (await _tokens_for(factory, 1, PURPOSE_CHANGE))[0].status == STATUS_USED
    # ... but the revert token stays pending (the "this wasn't me" window).
    assert (await _tokens_for(factory, 1, PURPOSE_REVERT))[0].status == STATUS_PENDING


async def _revert_cancels_pending_change(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    send, _ = _recorder()
    monkeypatch.setattr(mail_service, "send_mail", send)
    _env(
        monkeypatch,
        SMTP_ENABLED="true",
        EMAIL_CHANGE_CONFIRMATION="true",
        EMAIL_REGISTER_CONFIRMATION="false",
    )
    await _mk_user(factory, 1, email="old@test.local")
    async with factory() as db:
        user = await db.get(User, 1)
        assert user is not None
        assert (await auth_service.change_email(db, user, "new@test.local")) == "confirmation_sent"
    # The change is still pending; the old-address owner reverts it.
    revert_tok = (await _tokens_for(factory, 1, PURPOSE_REVERT))[0]
    async with factory() as db:
        await auth_service.revert_email_change(db, revert_tok.token)
    # No email moved ...
    assert (await _get_user(factory, 1)).email == "old@test.local"
    # ... the paired change token was cancelled (revoked) and the revert used.
    assert (await _tokens_for(factory, 1, PURPOSE_CHANGE))[0].status == STATUS_REVOKED
    assert (await _tokens_for(factory, 1, PURPOSE_REVERT))[0].status == STATUS_USED


async def _revert_switches_back_after_confirm(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    send, _ = _recorder()
    monkeypatch.setattr(mail_service, "send_mail", send)
    _env(
        monkeypatch,
        SMTP_ENABLED="true",
        EMAIL_CHANGE_CONFIRMATION="true",
        EMAIL_REGISTER_CONFIRMATION="false",
    )
    await _mk_user(factory, 1, email="old@test.local")
    async with factory() as db:
        user = await db.get(User, 1)
        assert user is not None
        assert (await auth_service.change_email(db, user, "new@test.local")) == "confirmation_sent"
    # The new address confirms the change (email is now new@test.local) ...
    change_tok = (await _tokens_for(factory, 1, PURPOSE_CHANGE))[0]
    async with factory() as db:
        await auth_service.confirm_email_token(db, change_tok.token)
    assert (await _get_user(factory, 1)).email == "new@test.local"
    # ... then the old-address owner reverts: the email switches back.
    revert_tok = (await _tokens_for(factory, 1, PURPOSE_REVERT))[0]
    async with factory() as db:
        await auth_service.revert_email_change(db, revert_tok.token)
    assert (await _get_user(factory, 1)).email == "old@test.local"
    assert (await _tokens_for(factory, 1, PURPOSE_REVERT))[0].status == STATUS_USED


async def _revert_rejects_invalid_and_conflicts(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    send, _ = _recorder()
    monkeypatch.setattr(mail_service, "send_mail", send)
    _env(
        monkeypatch,
        SMTP_ENABLED="true",
        EMAIL_CHANGE_CONFIRMATION="true",
        EMAIL_REGISTER_CONFIRMATION="false",
    )
    await _mk_user(factory, 1, email="old@test.local")
    async with factory() as db:
        user = await db.get(User, 1)
        assert user is not None
        assert (await auth_service.change_email(db, user, "new@test.local")) == "confirmation_sent"
    # A garbage token 404s.
    with pytest.raises(NotFound):
        async with factory() as db:
            await auth_service.revert_email_change(db, "not-a-real-token")
    # A revert cancels the pending change; a second use of the same token 409s.
    revert_tok = (await _tokens_for(factory, 1, PURPOSE_REVERT))[0]
    async with factory() as db:
        await auth_service.revert_email_change(db, revert_tok.token)
    with pytest.raises(Conflict):
        async with factory() as db:
            await auth_service.revert_email_change(db, revert_tok.token)


async def _revert_409_when_old_address_taken(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    send, _ = _recorder()
    monkeypatch.setattr(mail_service, "send_mail", send)
    _env(
        monkeypatch,
        SMTP_ENABLED="true",
        EMAIL_CHANGE_CONFIRMATION="true",
        EMAIL_REGISTER_CONFIRMATION="false",
    )
    await _mk_user(factory, 1, email="old@test.local")
    await _mk_user(factory, 2, email="other@test.local")
    async with factory() as db:
        user = await db.get(User, 1)
        assert user is not None
        assert (await auth_service.change_email(db, user, "new@test.local")) == "confirmation_sent"
    # The new address confirms the change (old@test.local is now free) ...
    change_tok = (await _tokens_for(factory, 1, PURPOSE_CHANGE))[0]
    async with factory() as db:
        await auth_service.confirm_email_token(db, change_tok.token)
    # ... and another account takes the previous address.
    async with factory() as db:
        other = await db.get(User, 2)
        assert other is not None
        other.email = "old@test.local"
        await db.commit()
    # Reverting would switch back to a taken address, so it 409s.
    revert_tok = (await _tokens_for(factory, 1, PURPOSE_REVERT))[0]
    with pytest.raises(Conflict):
        async with factory() as db:
            await auth_service.revert_email_change(db, revert_tok.token)


async def _email_change_inactive_applies_immediately(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    send, sent = _recorder()
    monkeypatch.setattr(mail_service, "send_mail", send)
    _env(monkeypatch, SMTP_ENABLED="true", EMAIL_CHANGE_CONFIRMATION="false")
    await _mk_user(factory, 1, email="old@test.local")
    async with factory() as db:
        user = await db.get(User, 1)
        assert user is not None
        status = await auth_service.change_email(db, user, "new@test.local")
    assert status == "changed"
    assert (await _get_user(factory, 1)).email == "new@test.local"
    assert await _tokens_for(factory, 1, PURPOSE_CHANGE) == []
    assert sent == []


async def _email_change_rejects_same_and_taken(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, SMTP_ENABLED="true", EMAIL_CHANGE_CONFIRMATION="false")
    await _mk_user(factory, 1, email="a@test.local")
    await _mk_user(factory, 2, email="b@test.local")
    async with factory() as db:
        user = await db.get(User, 1)
        other = await db.get(User, 2)
        assert user is not None and other is not None
        # Changing to the email already in use 409s ...
        with pytest.raises(Conflict):
            await auth_service.change_email(db, user, "a@test.local")
        # ... as does taking another account's email.
        with pytest.raises(Conflict):
            await auth_service.change_email(db, user, other.email)


# --- 5. token lifecycle ------------------------------------------------------


async def _confirm_marks_used_and_is_one_shot(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    send, _ = _recorder()
    monkeypatch.setattr(mail_service, "send_mail", send)
    _env(monkeypatch, SMTP_ENABLED="true", EMAIL_REGISTER_CONFIRMATION="true")
    async with factory() as db:
        user_id = (await auth_service.register(db, "carol@test.local", "password1", None)).id
    token = (await _tokens_for(factory, user_id, PURPOSE_REGISTER))[0]
    async with factory() as db:
        await auth_service.confirm_email_token(db, token.token)
    # A second confirm of the same token 409s (already used).
    with pytest.raises(Conflict):
        async with factory() as db:
            await auth_service.confirm_email_token(db, token.token)
    # A garbage token 404s.
    with pytest.raises(NotFound):
        async with factory() as db:
            await auth_service.confirm_email_token(db, "not-a-real-token")


async def _resend_revokes_previous_pending(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    send, _ = _recorder()
    monkeypatch.setattr(mail_service, "send_mail", send)
    _env(monkeypatch, SMTP_ENABLED="true", EMAIL_REGISTER_CONFIRMATION="true")
    async with factory() as db:
        user_id = (await auth_service.register(db, "dave@test.local", "password1", None)).id
    first = (await _tokens_for(factory, user_id, PURPOSE_REGISTER))[0]
    # Resend reissues: the old pending token is revoked, a new one is pending.
    async with factory() as db:
        u = await db.get(User, user_id)
        assert u is not None
        await auth_service.resend_confirmation(db, u, PURPOSE_REGISTER)
    tokens = await _tokens_for(factory, user_id, PURPOSE_REGISTER)
    assert len(tokens) == 2
    by_id = {t.id: t for t in tokens}
    assert by_id[first.id].status == STATUS_REVOKED
    assert sum(1 for t in tokens if t.status == STATUS_PENDING) == 1
    new = next(t for t in tokens if t.id != first.id)
    # The revoked token can no longer be confirmed; the fresh one can.
    with pytest.raises(Conflict):
        async with factory() as db:
            await auth_service.confirm_email_token(db, first.token)
    async with factory() as db:
        confirmed = await auth_service.confirm_email_token(db, new.token)
        assert confirmed.email_verified is True


async def _expired_token_is_rejected(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, SMTP_ENABLED="true", EMAIL_REGISTER_CONFIRMATION="true")
    await _mk_user(factory, 1, email_verified=False)
    # A pending token whose expiry is in the past.
    async with factory() as db:
        db.add(
            EmailToken(
                user_id=1,
                purpose=PURPOSE_REGISTER,
                token="expired-token-0001",
                target_email="u1@test.local",
                status=STATUS_PENDING,
                expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=1),
            )
        )
        await db.commit()
    with pytest.raises(Conflict):
        async with factory() as db:
            await auth_service.confirm_email_token(db, "expired-token-0001")


# --- 5b. password-change confirmation ----------------------------------------


async def _password_change_confirmation_active(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    send, sent = _recorder()
    monkeypatch.setattr(mail_service, "send_mail", send)
    _env(
        monkeypatch,
        SMTP_ENABLED="true",
        PASSWORD_CHANGE_CONFIRMATION="true",
        EMAIL_CHANGE_CONFIRMATION="false",
    )
    await _mk_user(factory, 1, email="pw@test.local", password="password1")
    async with factory() as db:
        user = await db.get(User, 1)
        assert user is not None
        status = await auth_service.change_password(db, user, "newpassword1")
    assert status == "confirmation_sent"
    # The password is NOT changed yet; a pending password token carries the
    # new-password hash, and the mail went to the current address.
    fresh = await _get_user(factory, 1)
    assert verify_password("password1", fresh.password_hash) is True
    assert verify_password("newpassword1", fresh.password_hash) is False
    tokens = await _tokens_for(factory, 1, PURPOSE_PASSWORD)
    assert len(tokens) == 1
    assert tokens[0].status == STATUS_PENDING
    assert tokens[0].target_email == "pw@test.local"
    assert tokens[0].password_hash is not None
    assert verify_password("newpassword1", tokens[0].password_hash)
    assert len(sent) == 1
    assert sent[0][0] == "pw@test.local"

    # Confirming applies the pending hash and marks the token used.
    async with factory() as db:
        await auth_service.confirm_email_token(db, tokens[0].token)
    fresh = await _get_user(factory, 1)
    assert verify_password("password1", fresh.password_hash) is False
    assert verify_password("newpassword1", fresh.password_hash) is True
    assert (await _tokens_for(factory, 1, PURPOSE_PASSWORD))[0].status == STATUS_USED


async def _password_change_inactive_applies_immediately(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    send, sent = _recorder()
    monkeypatch.setattr(mail_service, "send_mail", send)
    _env(monkeypatch, SMTP_ENABLED="true", PASSWORD_CHANGE_CONFIRMATION="false")
    await _mk_user(factory, 1, password="password1")
    async with factory() as db:
        user = await db.get(User, 1)
        assert user is not None
        status = await auth_service.change_password(db, user, "newpassword1")
    assert status == "changed"
    assert verify_password("newpassword1", (await _get_user(factory, 1)).password_hash) is True
    assert await _tokens_for(factory, 1, PURPOSE_PASSWORD) == []
    assert sent == []


async def _password_change_validations(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, SMTP_ENABLED="true", PASSWORD_CHANGE_CONFIRMATION="false")
    await _mk_user(factory, 1, password="password1")
    async with factory() as db:
        user = await db.get(User, 1)
        assert user is not None
        # Too short 422s ...
        with pytest.raises(ValidationFailed):
            await auth_service.change_password(db, user, "short")
        # ... and keeping the current password 409s.
        with pytest.raises(Conflict):
            await auth_service.change_password(db, user, "password1")
    # Nothing changed.
    assert verify_password("password1", (await _get_user(factory, 1)).password_hash) is True


async def _password_change_reissue_revokes_previous(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    send, _ = _recorder()
    monkeypatch.setattr(mail_service, "send_mail", send)
    _env(monkeypatch, SMTP_ENABLED="true", PASSWORD_CHANGE_CONFIRMATION="true")
    await _mk_user(factory, 1, password="password1")
    async with factory() as db:
        user = await db.get(User, 1)
        assert user is not None
        await auth_service.change_password(db, user, "newpassword1")
        # A second request supersedes the first pending token.
        await auth_service.change_password(db, user, "newpassword2")
    tokens = await _tokens_for(factory, 1, PURPOSE_PASSWORD)
    assert len(tokens) == 2
    first = tokens[0]
    assert first.status == STATUS_REVOKED
    new = next(t for t in tokens if t.status == STATUS_PENDING)
    # The superseded token can no longer be confirmed; the fresh one applies
    # the LATEST requested password (not the first one).
    with pytest.raises(Conflict):
        async with factory() as db:
            await auth_service.confirm_email_token(db, first.token)
    async with factory() as db:
        await auth_service.confirm_email_token(db, new.token)
    fresh = await _get_user(factory, 1)
    assert verify_password("newpassword2", fresh.password_hash) is True
    assert verify_password("newpassword1", fresh.password_hash) is False


async def _password_token_expired_and_one_shot(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, SMTP_ENABLED="true", PASSWORD_CHANGE_CONFIRMATION="false")
    await _mk_user(factory, 1, password="password1")
    pending_hash = hash_password("newpassword1")
    async with factory() as db:
        db.add(
            EmailToken(
                user_id=1,
                purpose=PURPOSE_PASSWORD,
                token="pw-expired-0001",
                target_email="u1@test.local",
                status=STATUS_PENDING,
                expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=1),
                password_hash=pending_hash,
            )
        )
        db.add(
            EmailToken(
                user_id=1,
                purpose=PURPOSE_PASSWORD,
                token="pw-once-000001",
                target_email="u1@test.local",
                status=STATUS_USED,
                expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
                password_hash=pending_hash,
            )
        )
        await db.commit()
    # Expired 409s; already-used 409s.
    with pytest.raises(Conflict):
        async with factory() as db:
            await auth_service.confirm_email_token(db, "pw-expired-0001")
    with pytest.raises(Conflict):
        async with factory() as db:
            await auth_service.confirm_email_token(db, "pw-once-000001")
    # The password never changed.
    assert verify_password("password1", (await _get_user(factory, 1)).password_hash) is True


# --- 5c. register preferred_language -----------------------------------------


async def _register_with_preferred_language(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, SMTP_ENABLED="false")
    async with factory() as db:
        ru_id = (
            await auth_service.register(db, "ru@test.local", "password1", None, "ru")
        ).id
        en_id = (
            await auth_service.register(db, "en@test.local", "password1", None)
        ).id
    assert (await _get_user(factory, ru_id)).preferred_language == "ru"
    # Unsupplied defaults to "en".
    assert (await _get_user(factory, en_id)).preferred_language == "en"
    # An invalid language 422s and creates no user.
    with pytest.raises(ValidationFailed):
        async with factory() as db:
            await auth_service.register(db, "fr@test.local", "password1", None, "fr")


# --- 6. SMTP kill switch -----------------------------------------------------


async def _smtp_kill_switch_is_a_noop(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With ``SMTP_ENABLED=false`` (even when ``SMTP_HOST`` is set) ``send_mail``
    must not touch the SMTP transport at all."""
    calls: list[str] = []

    async def _boom(*args: Any, **kwargs: Any) -> None:
        calls.append("called")

    # Point the real transport at a sentinel that fails if it is ever invoked.
    monkeypatch.setattr(mail_service.aiosmtplib, "send", _boom)
    _env(monkeypatch, SMTP_ENABLED="false", SMTP_HOST="smtp.example.com")
    # No exception, and the transport was never reached.
    await mail_service.send_mail("x@test.local", "subject", "body")
    assert calls == []


async def _smtp_off_skips_register_confirmation(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The master switch dominates the dedicated toggle: even with
    ``EMAIL_REGISTER_CONFIRMATION=true``, a disabled SMTP verifies on the spot."""
    send, sent = _recorder()
    monkeypatch.setattr(mail_service, "send_mail", send)
    _env(
        monkeypatch,
        SMTP_ENABLED="false",
        SMTP_HOST="smtp.example.com",
        EMAIL_REGISTER_CONFIRMATION="true",
    )
    async with factory() as db:
        user_id = (await auth_service.register(db, "erin@test.local", "password1", None)).id
    assert (await _get_user(factory, user_id)).email_verified is True
    assert await _tokens_for(factory, user_id, PURPOSE_REGISTER) == []
    assert sent == []


# --- 7. avatars --------------------------------------------------------------


_PNG = b"\x89PNG\r\n\x1a\n" + b"avatar-bytes"


async def _avatar_set_and_clear(factory: async_sessionmaker) -> None:
    user = await _mk_user(factory, 1)
    # No avatar -> decode is None.
    assert avatar_service.decode_avatar(user) is None
    # Set a valid PNG: stored base64, content type recorded.
    async with factory() as db:
        u = await db.get(User, 1)
        assert u is not None
        await avatar_service.set_avatar(db, u, _PNG, "image/png")
    fresh = await _get_user(factory, 1)
    assert fresh.avatar_content_type == "image/png"
    assert fresh.avatar_data is not None
    raw, ctype = avatar_service.decode_avatar(fresh)
    assert raw == _PNG
    assert ctype == "image/png"
    # Clear: both fields back to NULL.
    async with factory() as db:
        u = await db.get(User, 1)
        assert u is not None
        await avatar_service.clear_avatar(db, u)
    cleared = await _get_user(factory, 1)
    assert cleared.avatar_data is None
    assert cleared.avatar_content_type is None
    assert avatar_service.decode_avatar(cleared) is None


async def _avatar_rejects_bad_type_and_oversize(
    factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, MAX_AVATAR_MB="1")
    await _mk_user(factory, 1)
    async with factory() as db:
        u = await db.get(User, 1)
        assert u is not None
        # A non-image content type 422s.
        with pytest.raises(ValidationFailed):
            await avatar_service.set_avatar(db, u, b"pdf-bytes", "application/pdf")
        # An over-limit image 422s (1 MiB + 5 > the 1 MiB cap).
        with pytest.raises(ValidationFailed):
            await avatar_service.set_avatar(
                db, u, b"\x89PNG" + b"\x00" * (1024 * 1024), "image/png"
            )
    # Nothing was stored.
    assert (await _get_user(factory, 1)).avatar_data is None


# --- 8. admin create / patch / lockout / stats ------------------------------


async def _admin_create_user_without_confirmation(factory: async_sessionmaker) -> None:
    async with factory() as db:
        created_id = (
            await admin_service.create_user(
                db, "newbie@test.local", "password1", "Newbie"
            )
        ).id
    # Admin-created accounts are verified (no token flow) and not admins.
    fresh = await _get_user(factory, created_id)
    assert fresh.email_verified is True
    assert fresh.is_admin is False
    assert verify_password("password1", fresh.password_hash)
    # Duplicate email 409s.
    with pytest.raises(Conflict):
        async with factory() as db:
            await admin_service.create_user(db, "newbie@test.local", "password1", None)


async def _admin_patch_user_fields(factory: async_sessionmaker) -> None:
    await _mk_user(factory, 1, email="a@test.local")
    async with factory() as db:
        await admin_service.patch_user(
            db,
            1,
            display_name="Renamed",
            email="a2@test.local",
            password="newpassword",
            is_admin=True,
        )
    fresh = await _get_user(factory, 1)
    assert fresh.display_name == "Renamed"
    assert fresh.email == "a2@test.local"
    assert fresh.is_admin is True
    # The old password no longer works; the new one does.
    assert verify_password("password1", fresh.password_hash) is False
    assert verify_password("newpassword", fresh.password_hash) is True
    # Patching a missing user 404s.
    with pytest.raises(NotFound):
        async with factory() as db:
            await admin_service.patch_user(db, 9999, display_name="Ghost")


async def _admin_last_admin_cannot_be_demoted(factory: async_sessionmaker) -> None:
    await _mk_user(factory, 1, is_admin=True)
    # The single remaining admin cannot be demoted (lockout protection).
    with pytest.raises(Conflict):
        async with factory() as db:
            await admin_service.patch_user(db, 1, is_admin=False)
    assert (await _get_user(factory, 1)).is_admin is True
    # With a second admin present, the first may be demoted freely.
    await _mk_user(factory, 2, is_admin=True)
    async with factory() as db:
        await admin_service.patch_user(db, 1, is_admin=False)
    assert (await _get_user(factory, 1)).is_admin is False


async def _admin_stats_aggregate(factory: async_sessionmaker) -> None:
    # 3 users (1 admin, 1 unverified), 1 active + 1 closed group,
    # 1 active expense (10.00 USD), 1 confirmed settlement (4.00 USD).
    await _mk_user(factory, 1, is_admin=True)
    await _mk_user(factory, 2, email_verified=False)
    await _mk_user(factory, 3)
    async with factory() as db:
        db.add(Group(id=1, name="A", currency="USD", created_by=1, status="active"))
        db.add(Group(id=2, name="B", currency="USD", created_by=1, status="closed"))
        db.add(Membership(group_id=1, user_id=1))
        db.add(Membership(group_id=1, user_id=2))
        db.add(
            Expense(
                id=1,
                group_id=1,
                payer_id=1,
                created_by=1,
                description="Lunch",
                amount=DECIMAL_10,
                split_method="equal",
                category="food",
                date=dt.date(2026, 10, 1),
            )
        )
        await db.flush()
        db.add(ExpenseParticipant(expense_id=1, user_id=2, share=DECIMAL_10))
        db.add(
            Settlement(
                id=1,
                group_id=1,
                from_user_id=2,
                to_user_id=1,
                amount=DECIMAL_4,
                state="confirmed",
            )
        )
        await db.commit()

    async with factory() as db:
        stats = await admin_service.get_stats(db)
    assert stats.users_total == 3
    assert stats.users_admin == 1
    assert stats.users_unverified == 1
    assert stats.groups_active == 1
    assert stats.groups_closed == 1
    assert stats.expenses_total == 1
    assert stats.expenses_by_currency == (("USD", 1, DECIMAL_10),)
    assert stats.settlements_by_state == {"confirmed": 1}
    assert stats.settlements_confirmed_by_currency == (("USD", DECIMAL_4),)


# --- test entry points -------------------------------------------------------


def test_first_ever_user_is_admin(run_db: Any) -> None:
    run_db(_first_ever_user_is_admin)


def test_migration_backfills_lowest_id_admin(run_db: Any) -> None:
    run_db(_migration_backfills_lowest_id_admin)


def test_registration_closed_after_first_user(
    run_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _registration_closed_after_first_user(factory, monkeypatch)

    run_db(_s)


def test_registration_open_ignores_user_count(
    run_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _registration_open_ignores_user_count(factory, monkeypatch)

    run_db(_s)


def test_registration_status_true_on_empty_db(
    run_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _registration_status_true_on_empty_db(factory, monkeypatch)

    run_db(_s)


def test_register_confirmation_active(run_db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _register_confirmation_active(factory, monkeypatch)

    run_db(_s)


def test_register_confirmation_inactive(
    run_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _register_confirmation_inactive(factory, monkeypatch)

    run_db(_s)


def test_email_change_confirmation_active(
    run_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _email_change_confirmation_active(factory, monkeypatch)

    run_db(_s)


def test_email_change_inactive_applies_immediately(
    run_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _email_change_inactive_applies_immediately(factory, monkeypatch)

    run_db(_s)


def test_email_change_rejects_same_and_taken(
    run_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _email_change_rejects_same_and_taken(factory, monkeypatch)

    run_db(_s)


def test_email_change_revert_cancels_pending(
    run_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _revert_cancels_pending_change(factory, monkeypatch)

    run_db(_s)


def test_email_change_revert_switches_back(
    run_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _revert_switches_back_after_confirm(factory, monkeypatch)

    run_db(_s)


def test_email_change_revert_invalid_and_conflicts(
    run_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _revert_rejects_invalid_and_conflicts(factory, monkeypatch)

    run_db(_s)


def test_email_change_revert_409_old_taken(
    run_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _revert_409_when_old_address_taken(factory, monkeypatch)

    run_db(_s)


def test_confirm_marks_used_and_is_one_shot(
    run_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _confirm_marks_used_and_is_one_shot(factory, monkeypatch)

    run_db(_s)


def test_resend_revokes_previous_pending(
    run_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _resend_revokes_previous_pending(factory, monkeypatch)

    run_db(_s)


def test_expired_token_is_rejected(run_db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _expired_token_is_rejected(factory, monkeypatch)

    run_db(_s)


def test_password_change_confirmation_active(
    run_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _password_change_confirmation_active(factory, monkeypatch)

    run_db(_s)


def test_password_change_inactive_applies_immediately(
    run_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _password_change_inactive_applies_immediately(factory, monkeypatch)

    run_db(_s)


def test_password_change_validations(
    run_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _password_change_validations(factory, monkeypatch)

    run_db(_s)


def test_password_change_reissue_revokes_previous(
    run_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _password_change_reissue_revokes_previous(factory, monkeypatch)

    run_db(_s)


def test_password_token_expired_and_one_shot(
    run_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _password_token_expired_and_one_shot(factory, monkeypatch)

    run_db(_s)


def test_register_with_preferred_language(
    run_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _register_with_preferred_language(factory, monkeypatch)

    run_db(_s)


def test_smtp_kill_switch_is_a_noop(run_db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _smtp_kill_switch_is_a_noop(factory, monkeypatch)

    run_db(_s)


def test_smtp_off_skips_register_confirmation(
    run_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _smtp_off_skips_register_confirmation(factory, monkeypatch)

    run_db(_s)


def test_avatar_set_and_clear(run_db: Any) -> None:
    run_db(_avatar_set_and_clear)


def test_avatar_rejects_bad_type_and_oversize(
    run_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _s(factory: async_sessionmaker) -> None:
        await _avatar_rejects_bad_type_and_oversize(factory, monkeypatch)

    run_db(_s)


def test_admin_create_user_without_confirmation(run_db: Any) -> None:
    run_db(_admin_create_user_without_confirmation)


def test_admin_patch_user_fields(run_db: Any) -> None:
    run_db(_admin_patch_user_fields)


def test_admin_last_admin_cannot_be_demoted(run_db: Any) -> None:
    run_db(_admin_last_admin_cannot_be_demoted)


def test_admin_stats_aggregate(run_db: Any) -> None:
    run_db(_admin_stats_aggregate)
