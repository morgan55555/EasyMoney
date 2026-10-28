"""Mail transport (AGENTS.md §3.4, §2.13).

``aiosmtplib`` when ``SMTP_HOST`` is set and ``SMTP_ENABLED`` is true;
otherwise log-only (dev or kill switch). Every mail path in the app goes
through this module — nothing else opens SMTP connections.

User-controlled values (group names, display names) are escaped with
``html.escape`` in the **HTML** bodies of all templates; the subjects and
plain-text bodies are plain-text context and are deliberately left
unescaped. Every HTML body is the branded app-styled envelope (§3.4): a
header with the app icon (inlined as a base64 data URI) and the EasyMoney
wordmark, a 600px-wide white card on a gray page, and an indigo CTA button
for the action link. Group-scoped mails (reminder, close summary) link to
the group page ``FRONTEND_URL/groups/{id}``, not the home page.

All message builders accept a ``lang`` parameter (``"en"`` | ``"ru"``);
recipients without an account default to ``"en"``.
"""

from __future__ import annotations

import base64
import logging
from decimal import Decimal
from email.message import EmailMessage
from html import escape
from pathlib import Path

import aiosmtplib

from app.core.config import get_settings
from app.services import currency as currency_service

logger = logging.getLogger("easymoney.mail")

DEFAULT_FROM = "EasyMoney <no-reply@easymoney.local>"

# The app icon ships with the code (COPY app ./app) and is inlined as a
# data URI, so no external image request is ever made.
_ICON_PATH = Path(__file__).resolve().parents[1] / "templates" / "mail" / "icon.png"
_icon_cache: str | None = None


def _icon_data_uri() -> str:
    """Base64 data URI of the app icon; loaded and cached once per process."""
    global _icon_cache
    if _icon_cache is None:
        payload = _ICON_PATH.read_bytes()
        _icon_cache = "data:image/png;base64," + base64.b64encode(payload).decode("ascii")
    return _icon_cache


# Frontend design tokens (the Tailwind palette the app uses), so mail reads
# like the product: gray-50 page, white card, gray-200 borders, indigo accent.
_PAGE_BG = "#f9fafb"
_CARD_BORDER = "#e5e7eb"
_SOFT_BORDER = "#f3f4f6"
_MUTED = "#6b7280"
_FAINT = "#9ca3af"
_BODY = "#374151"
_STRONG = "#111827"
_ACCENT = "#4f46e5"
_OK_BG = "#ecfdf5"
_OK_BORDER = "#a7f3d0"
_OK_TEXT = "#065f46"
# Single quotes around the multi-word family: the font stack is interpolated
# into double-quoted ``style="..."`` attributes, where double quotes would
# terminate the attribute and drop every declaration after it.
_FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"


# ---------------------------------------------------------------------------
# Localization (§2.13): EN + RU string sets
# ---------------------------------------------------------------------------

_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "footer": "EasyMoney &mdash; shared expenses, settled fairly.",
        "greeting": "Hi {name},",
        "greeting_no_name": "Hi,",
        "signoff": "— EasyMoney",
        "signoff_plain": "- EasyMoney",
        # invite
        "invite_subject": '{inviter} invited you to "{group}" on EasyMoney',
        "invite_body": '{inviter} invited you to join the group "{group}" on EasyMoney.',
        "invite_cta": "Accept the invitation",
        "invite_expiry": "This invitation expires in {days} days.",
        # overdue
        "overdue_subject": 'EasyMoney: you owe {total} in "{group}"',
        "overdue_body": 'Your debt in the group "{group}" is overdue.',
        "overdue_section": "You currently owe",
        "overdue_total_label": "Total",
        "overdue_cta": "Open the group in EasyMoney",
        "overdue_plaintext_label": "You currently owe:",
        "overdue_total_plain": "Total: {total}",
        "overdue_link_label": "Open the group in EasyMoney:",
        # close summary
        "close_subject": 'EasyMoney: final summary for "{group}"',
        "close_intro": 'The group "{group}" has been closed. Final summary:',
        "close_settled": "Everyone is settled up — no outstanding debts.",
        "close_section_balances": "Balances",
        "close_section_pairs": "Who owes whom",
        "close_section_transfers": "Suggested transfers",
        "close_owes": "{frm} owes {to}",
        "close_cta": "Open the group in EasyMoney",
        "close_plain_balances": "Balances:",
        "close_plain_pairs": "Who owes whom:",
        "close_plain_transfers": "Suggested transfers (the minimal set that settles everything):",
        # register confirmation
        "register_subject": "Confirm your email on EasyMoney",
        "register_body": (
            "Please confirm your email address to activate your EasyMoney account:"
        ),
        "register_cta": "Confirm email",
        # email-change confirmation (sent to the NEW address)
        "change_subject": "Confirm your new email address on EasyMoney",
        "change_body": (
            "Your EasyMoney account email is being changed to {new_email}. "
            "Click below to confirm the change. If you did not make this "
            "request, you can ignore this email."
        ),
        "change_cta": "Confirm new email",
        # email-change revert (sent to the PREVIOUS address)
        "revert_subject": "Your email address was changed on EasyMoney",
        "revert_body": (
            "Your email address was changed to {new_email}. If you made this "
            "change, you can ignore this email. If this wasn't you, click below "
            "to revert it."
        ),
        "revert_cta": "This wasn't me",
        # password-change confirmation
        "password_subject": "Confirm your password change on EasyMoney",
        "password_body": (
            "We received a request to change the password for your EasyMoney account. "
            "Click below to confirm. If you did not request this change, you can ignore "
            "this email."
        ),
        "password_cta": "Confirm password change",
    },
    "ru": {
        "footer": "EasyMoney — общие расходы, честный расчёт.",
        "greeting": "Привет, {name},",
        "greeting_no_name": "Привет,",
        "signoff": "— EasyMoney",
        "signoff_plain": "- EasyMoney",
        # invite
        "invite_subject": 'Вас пригласили в группу "{group}" в EasyMoney',
        "invite_body": '{inviter} пригласил(а) вас в группу "{group}" в EasyMoney.',
        "invite_cta": "Принять приглашение",
        "invite_expiry": "Приглашение действует {days} дн.",
        # overdue
        "overdue_subject": 'EasyMoney: вы должны {total} в "{group}"',
        "overdue_body": 'Ваш долг в группе "{group}" просрочен.',
        "overdue_section": "Вы должны",
        "overdue_total_label": "Итого",
        "overdue_cta": "Открыть группу в EasyMoney",
        "overdue_plaintext_label": "Вы должны:",
        "overdue_total_plain": "Итого: {total}",
        "overdue_link_label": "Открыть группу в EasyMoney:",
        # close summary
        "close_subject": 'EasyMoney: итоговый отчёт по "{group}"',
        "close_intro": 'Группа "{group}" закрыта. Итоговый отчёт:',
        "close_settled": "Все долги погашены — незакрытых долгов нет.",
        "close_section_balances": "Балансы",
        "close_section_pairs": "Кто кому должен",
        "close_section_transfers": "Рекомендуемые переводы",
        "close_owes": "{frm} должен(на) {to}",
        "close_cta": "Открыть группу в EasyMoney",
        "close_plain_balances": "Балансы:",
        "close_plain_pairs": "Кто кому должен:",
        "close_plain_transfers": "Рекомендуемые переводы (минимальный набор для полного расчёта):",
        # register confirmation
        "register_subject": "Подтвердите email в EasyMoney",
        "register_body": "Подтвердите адрес email, чтобы активировать аккаунт EasyMoney:",
        "register_cta": "Подтвердить email",
        # email-change confirmation (sent to the NEW address)
        "change_subject": "Подтвердите новый email в EasyMoney",
        "change_body": (
            "Email вашего аккаунта EasyMoney меняется на {new_email}. "
            "Нажмите ниже, чтобы подтвердить смену. Если вы не запрашивали "
            "смену, проигнорируйте это письмо."
        ),
        "change_cta": "Подтвердить новый email",
        # email-change revert (sent to the PREVIOUS address)
        "revert_subject": "Ваш email был изменён в EasyMoney",
        "revert_body": (
            "Ваш email был изменён на {new_email}. Если это сделали вы, "
            "проигнорируйте это письмо. Если это были не вы, нажмите ниже, "
            "чтобы отменить смену."
        ),
        "revert_cta": "Это не я",
        # password-change confirmation
        "password_subject": "Подтвердите смену пароля в EasyMoney",
        "password_body": (
            "Поступил запрос на смену пароля аккаунта EasyMoney. Нажмите ниже, "
            "чтобы подтвердить. Если вы не запрашивали смену пароля, проигнорируйте "
            "это письмо."
        ),
        "password_cta": "Подтвердить смену пароля",
    },
}


def _t(lang: str, key: str) -> str:
    """Look up a localized string; unknown languages fall back to English."""
    strings = _STRINGS.get(lang, _STRINGS["en"])
    return strings[key]


def _greeting(lang: str, name: str | None) -> str:
    """The personal greeting line (the plain-text twin is built by callers)."""
    line = _t(lang, "greeting").format(name=escape(name)) if name else _t(lang, "greeting_no_name")
    return (
        f'<p style="margin:0 0 12px;font-family:{_FONT};font-size:18px;font-weight:600;'
        f'color:{_STRONG};">{line}</p>'
    )


def _brand_header() -> str:
    """The app header, like the frontend top bar: icon + EasyMoney wordmark."""
    icon = _icon_data_uri()
    return (
        f'<div style="padding:20px 32px 18px;border-bottom:1px solid {_SOFT_BORDER};">'
        f'<table role="presentation" cellpadding="0" cellspacing="0"><tr>'
        f'<td style="vertical-align:middle;padding-right:14px;">'
        f'<img src="{icon}" width="56" height="56" alt="EasyMoney" '
        f'style="display:block;border-radius:14px;"></td>'
        f'<td style="vertical-align:middle;font-family:{_FONT};font-size:32px;'
        f'font-weight:800;letter-spacing:-0.025em;color:{_STRONG};">'
        f'Easy<span style="color:{_ACCENT};">Money</span></td>'
        f"</tr></table></div>"
    )


def _email_html(lang: str, body: str) -> str:
    """The branded envelope: gray page, 600px white card, header, body, footer."""
    footer = _t(lang, "footer")
    return (
        f'<div style="background-color:{_PAGE_BG};padding:24px 12px;">'
        f'<div style="max-width:600px;margin:0 auto;background-color:#ffffff;'
        f'border:1px solid {_CARD_BORDER};border-radius:12px;">'
        f"{_brand_header()}"
        f'<div style="padding:24px 32px 28px;font-family:{_FONT};color:{_BODY};'
        f'font-size:15px;line-height:1.6;">{body}</div>'
        f'<div style="padding:14px 32px;background-color:{_PAGE_BG};'
        f"border-top:1px solid {_SOFT_BORDER};border-radius:0 0 12px 12px;"
        f'font-family:{_FONT};color:{_FAINT};font-size:12px;">'
        f"{footer}"
        f"</div></div>"
    )


def _section(title: str) -> str:
    """A muted uppercase section label, like the app's panel headings."""
    return (
        f'<div style="margin:24px 0 0;padding-bottom:6px;border-bottom:1px solid {_CARD_BORDER};'
        f"font-family:{_FONT};font-size:12px;font-weight:600;"
        f'letter-spacing:0.05em;text-transform:uppercase;color:{_MUTED};">{title}</div>'
    )


def _money_row(label: str, amount: str, last: bool = False) -> str:
    """One label/amount row; amounts right-aligned, tabular, bold."""
    border = "" if last else f"border-bottom:1px solid {_CARD_BORDER};"
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="width:100%;border-collapse:collapse;"><tr>'
        f'<td style="padding:9px 0;font-family:{_FONT};font-size:15px;color:{_BODY};{border}">'
        f"{label}</td>"
        f'<td style="padding:9px 0;font-family:{_FONT};font-size:15px;font-weight:600;'
        f"text-align:right;white-space:nowrap;color:{_STRONG};"
        f'font-variant-numeric:tabular-nums;{border}">'
        f"{amount}</td>"
        f"</tr></table>"
    )


def _total_row(lang: str, amount: str) -> str:
    """The bold accent-colored total line under the owed list."""
    label = _t(lang, "overdue_total_label")
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="width:100%;border-collapse:collapse;"><tr>'
        f'<td style="padding:12px 0;border-top:1px solid {_CARD_BORDER};'
        f'font-family:{_FONT};font-size:15px;font-weight:700;color:{_STRONG};">{label}</td>'
        f'<td style="padding:12px 0;border-top:1px solid {_CARD_BORDER};'
        f"font-family:{_FONT};font-size:16px;font-weight:700;text-align:right;"
        f'white-space:nowrap;color:{_ACCENT};">{amount}</td>'
        f"</tr></table>"
    )


def _cta(url: str, label: str) -> str:
    """The primary action, styled like the app's indigo button."""
    return (
        f'<div style="margin-top:28px;">'
        f'<a href="{url}" style="display:inline-block;background-color:{_ACCENT};'
        f"color:#ffffff;font-family:{_FONT};font-size:15px;font-weight:600;"
        f'padding:10px 20px;border-radius:8px;text-decoration:none;">{label}</a>'
        f"</div>"
    )


def _group_url(group_id: int | None) -> str:
    """The group page URL; the app home page when no group id is given."""
    base = get_settings().frontend_url.rstrip("/")
    if group_id is None:
        return base
    return f"{base}/groups/{group_id}"


def _confirm_url(token: str) -> str:
    """The email-confirmation page URL (frontend route)."""
    return f"{get_settings().frontend_url.rstrip('/')}/auth/email/confirm?token={token}"


def _revert_url(token: str) -> str:
    """The email-change revert ("this wasn't me") page URL (frontend route)."""
    return f"{get_settings().frontend_url.rstrip('/')}/auth/email/revert?token={token}"


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


async def send_mail(to: str, subject: str, text: str, html: str | None = None) -> None:
    """Send one message; log it instead when mail is disabled or no host is set.

    ``SMTP_ENABLED=false`` makes this a log-only no-op **even when**
    ``SMTP_HOST`` is configured (the kill switch, §2.13).
    """
    settings = get_settings()
    if not settings.smtp_enabled:
        logger.info("[mail] (disabled) to=%s subject=%r", to, subject)
        return
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or DEFAULT_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    if settings.smtp_host:
        # Normalize empty strings to None: an empty ``SMTP_USER`` (common in
        # compose env blocks) would otherwise make aiosmtplib attempt AUTH
        # against servers that do not offer the extension.
        username = settings.smtp_user or None
        password = settings.smtp_password if username else None
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=username,
            password=password,
            use_tls=settings.smtp_use_tls,
        )
    else:
        logger.info("[mail] (log-only) to=%s subject=%r", to, subject)
        logger.info("[mail] body:\n%s", text)


# ---------------------------------------------------------------------------
# Money helper
# ---------------------------------------------------------------------------


def _money(amount: Decimal, currency: str) -> str:
    """Render money with its currency code: ``12.50 USD``, ``1250 JPY``."""
    return f"{currency_service.format_money(amount, currency)} {currency}"


# ---------------------------------------------------------------------------
# Message builders (each returns (subject, plain_text, html))
# ---------------------------------------------------------------------------


def invite_message(
    group_name: str,
    inviter_name: str,
    token: str,
    days: int,
    lang: str = "en",
) -> tuple[str, str, str]:
    """Subject/text/html for an email invitation (§2.10)."""
    link = f"{get_settings().frontend_url.rstrip('/')}/invite/{token}"
    subject = _t(lang, "invite_subject").format(inviter=inviter_name, group=group_name)
    text = (
        f"{_t(lang, 'greeting_no_name')}\n\n"
        + _t(lang, "invite_body").format(inviter=inviter_name, group=group_name)
        + f"\n\nAccept the invitation:\n{link}\n\n"
        + _t(lang, "invite_expiry").format(days=days)
        + f"\n\n{_t(lang, 'signoff')}"
    )
    body_text_html = _t(lang, "invite_body").format(
        inviter=escape(inviter_name), group=escape(group_name)
    )
    body = (
        _greeting(lang, None)
        + f'<p style="margin:0 0 8px;">{body_text_html}</p>'
        + _cta(link, _t(lang, "invite_cta"))
        + f'<p style="margin:16px 0 0;font-size:13px;color:{_MUTED};">'
        + _t(lang, "invite_expiry").format(days=days)
        + "</p>"
    )
    return subject, text, _email_html(lang, body)


def overdue_reminder_message(
    group_name: str,
    currency: str,
    debtor_name: str,
    per_creditor: list[tuple[str, Decimal]],
    total: Decimal,
    group_id: int | None = None,
    lang: str = "en",
) -> tuple[str, str, str]:
    """Subject/text/html for an overdue-debt reminder (§2.7 step 4).

    ``per_creditor`` lists each creditor the debtor currently owes with the
    pairwise amount (it sums to ``total``). ``group_id`` links the CTA to the
    group page; without it the CTA falls back to the app home page.
    """
    group_url = _group_url(group_id)
    total_str = _money(total, currency)
    subject = _t(lang, "overdue_subject").format(total=total_str, group=group_name)
    items = "\n".join(f"  - {name}: {_money(amount, currency)}" for name, amount in per_creditor)
    text = (
        f"{_t(lang, 'greeting').format(name=debtor_name)}\n\n"
        + _t(lang, "overdue_body").format(group=group_name)
        + f"\n\n{_t(lang, 'overdue_plaintext_label')}\n{items}\n\n"
        + _t(lang, "overdue_total_plain").format(total=total_str)
        + f"\n\n{_t(lang, 'overdue_link_label')}\n{group_url}\n\n"
        + f"{_t(lang, 'signoff_plain')}"
    )
    rows = "".join(
        _money_row(escape(name), _money(amount, currency), last=(i == len(per_creditor) - 1))
        for i, (name, amount) in enumerate(per_creditor)
    )
    overdue_body_text = escape(_t(lang, "overdue_body").format(group=group_name))
    body = (
        _greeting(lang, debtor_name)
        + f'<p style="margin:0 0 8px;">{overdue_body_text}</p>'
        + _section(_t(lang, "overdue_section"))
        + rows
        + _total_row(lang, total_str)
        + _cta(group_url, _t(lang, "overdue_cta"))
    )
    return subject, text, _email_html(lang, body)


def close_summary_message(
    group_name: str,
    currency: str,
    balances: list[tuple[str, Decimal]],
    pairs: list[tuple[str, str, Decimal]],
    transfers: list[tuple[str, str, Decimal]],
    recipient_name: str | None = None,
    group_id: int | None = None,
    lang: str = "en",
) -> tuple[str, str, str]:
    """Subject/text/html for the group-close final summary (§2.9).

    ``balances``: all member balances — zero-balance ones included, since the
    summary is a complete statement (§2.9); ``pairs``: the who-owes-whom view;
    ``transfers``: the suggested minimal transfers. Empty ``transfers`` means
    everyone is settled up. ``recipient_name`` is the recipient's display name
    (the greeting is personal; one mail is built per member). ``group_id``
    links the CTA to the group page; without it, the app home page.
    """
    group_url = _group_url(group_id)
    greeting = (
        _t(lang, "greeting").format(name=recipient_name)
        if recipient_name
        else _t(lang, "greeting_no_name")
    )
    link_text = f"{_t(lang, 'overdue_link_label')}\n{group_url}\n\n"
    subject = _t(lang, "close_subject").format(group=group_name)
    intro = (
        '<p style="margin:0 0 8px;">'
        + escape(_t(lang, "close_intro").format(group=group_name))
        + "</p>"
    )
    if not transfers:
        text = (
            f"{greeting}\n\n"
            + _t(lang, "close_intro").format(group=group_name)
            + f"\n\n{_t(lang, 'close_settled')}\n\n"
            f"{link_text}"
            f"{_t(lang, 'signoff_plain')}"
        )
        body = (
            _greeting(lang, recipient_name)
            + intro
            + f'<div style="margin-top:8px;padding:12px 16px;background-color:{_OK_BG};'
            + f'border:1px solid {_OK_BORDER};border-radius:8px;color:{_OK_TEXT};font-size:14px;">'
            + f"{escape(_t(lang, 'close_settled'))}</div>"
            + _cta(group_url, _t(lang, "close_cta"))
        )
        return subject, text, _email_html(lang, body)
    balance_rows = "".join(
        _money_row(escape(name), _money(amount, currency), last=(i == len(balances) - 1))
        for i, (name, amount) in enumerate(balances)
    )
    pair_rows = "".join(
        _money_row(
            escape(_t(lang, "close_owes").format(frm=escape(frm), to=escape(to))),
            _money(amount, currency),
            last=(i == len(pairs) - 1),
        )
        for i, (frm, to, amount) in enumerate(pairs)
    )
    transfer_rows = "".join(
        _money_row(
            f"{escape(frm)} &rarr; {escape(to)}",
            _money(amount, currency),
            last=(i == len(transfers) - 1),
        )
        for i, (frm, to, amount) in enumerate(transfers)
    )
    balance_items = "\n".join(
        f"  - {name}: {_money(amount, currency)}" for name, amount in balances
    )
    pair_items = "\n".join(
        f"  - {_t(lang, 'close_owes').format(frm=frm, to=to)} {_money(amount, currency)}"
        for frm, to, amount in pairs
    )
    transfer_items = "\n".join(
        f"  - {frm} -> {to}: {_money(amount, currency)}" for frm, to, amount in transfers
    )
    text = (
        f"{greeting}\n\n"
        + _t(lang, "close_intro").format(group=group_name)
        + f"\n\n{_t(lang, 'close_plain_balances')}\n{balance_items}\n\n"
        + f"{_t(lang, 'close_plain_pairs')}\n{pair_items}\n\n"
        + f"{_t(lang, 'close_plain_transfers')}\n{transfer_items}\n\n"
        f"{link_text}"
        f"{_t(lang, 'signoff_plain')}"
    )
    body = (
        _greeting(lang, recipient_name)
        + intro
        + _section(_t(lang, "close_section_balances"))
        + balance_rows
        + _section(_t(lang, "close_section_pairs"))
        + pair_rows
        + _section(_t(lang, "close_section_transfers"))
        + transfer_rows
        + _cta(group_url, _t(lang, "close_cta"))
    )
    return subject, text, _email_html(lang, body)


# ---------------------------------------------------------------------------
# New confirmation emails (§2.13)
# ---------------------------------------------------------------------------


def register_confirmation_message(
    user_name: str,
    token: str,
    lang: str = "en",
) -> tuple[str, str, str]:
    """Subject/text/html for the registration confirmation email (§2.13)."""
    link = _confirm_url(token)
    subject = _t(lang, "register_subject")
    text = (
        f"{_t(lang, 'greeting').format(name=user_name)}\n\n"
        + _t(lang, "register_body")
        + f"\n{link}\n\n"
        + f"{_t(lang, 'signoff_plain')}"
    )
    body = (
        _greeting(lang, user_name)
        + f'<p style="margin:0 0 8px;">{escape(_t(lang, "register_body"))}</p>'
        + _cta(link, _t(lang, "register_cta"))
    )
    return subject, text, _email_html(lang, body)


def change_confirmation_message(
    user_name: str,
    new_email: str,
    token: str,
    lang: str = "en",
) -> tuple[str, str, str]:
    """Subject/text/html for the email-change confirmation (§2.13).

    Sent to the **new** address. The token links the owner of that address to
    the confirm endpoint, which applies the swap.
    """
    link = _confirm_url(token)
    subject = _t(lang, "change_subject")
    body_text = _t(lang, "change_body").format(new_email=new_email)
    text = (
        f"{_t(lang, 'greeting').format(name=user_name)}\n\n"
        + f"{body_text}\n"
        + f"{link}\n\n"
        + f"{_t(lang, 'signoff_plain')}"
    )
    body = (
        _greeting(lang, user_name)
        + f'<p style="margin:0 0 8px;">{escape(body_text)}</p>'
        + _cta(link, _t(lang, "change_cta"))
    )
    return subject, text, _email_html(lang, body)


def change_revert_message(
    user_name: str,
    new_email: str,
    token: str,
    lang: str = "en",
) -> tuple[str, str, str]:
    """Subject/text/html for the email-change revert notice (§2.13).

    Sent to the **previous** address. The token links the owner of that address
    to the revert endpoint, which cancels the pending change (or switches the
    email back to this address) within the ``EMAIL_CHANGE_REVERT_DAYS`` window.
    """
    link = _revert_url(token)
    subject = _t(lang, "revert_subject")
    body_text = _t(lang, "revert_body").format(new_email=new_email)
    text = (
        f"{_t(lang, 'greeting').format(name=user_name)}\n\n"
        + f"{body_text}\n"
        + f"{link}\n\n"
        + f"{_t(lang, 'signoff_plain')}"
    )
    body = (
        _greeting(lang, user_name)
        + f'<p style="margin:0 0 8px;">{escape(body_text)}</p>'
        + _cta(link, _t(lang, "revert_cta"))
    )
    return subject, text, _email_html(lang, body)


def password_confirmation_message(
    user_name: str,
    token: str,
    lang: str = "en",
) -> tuple[str, str, str]:
    """Subject/text/html for the password-change confirmation email (§2.13).

    Sent to the user's **current** address. The token carries the pending
    new-password hash; the confirm endpoint applies it on success.
    """
    link = _confirm_url(token)
    subject = _t(lang, "password_subject")
    text = (
        f"{_t(lang, 'greeting').format(name=user_name)}\n\n"
        + _t(lang, "password_body")
        + f"\n{link}\n\n"
        + f"{_t(lang, 'signoff_plain')}"
    )
    body = (
        _greeting(lang, user_name)
        + f'<p style="margin:0 0 8px;">{escape(_t(lang, "password_body"))}</p>'
        + _cta(link, _t(lang, "password_cta"))
    )
    return subject, text, _email_html(lang, body)
