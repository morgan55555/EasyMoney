"""Mail transport (AGENTS.md §3.4).

``aiosmtplib`` when ``SMTP_HOST`` is set; otherwise log-only (dev). Every
mail path in the app goes through this module — nothing else opens SMTP
connections.

User-controlled values (group names, display names) are escaped with
``html.escape`` in the **HTML** bodies of all templates; the subjects and
plain-text bodies are plain-text context and are deliberately left
unescaped. Every HTML body is the branded app-styled envelope (§3.4): a
header with the app icon (inlined as a base64 data URI) and the EasyMoney
wordmark, a 600px-wide white card on a gray page, and an indigo CTA button
for the action link. Group-scoped mails (reminder, close summary) link to
the group page ``FRONTEND_URL/groups/{id}``, not the home page.
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


def _email_html(body: str) -> str:
    """The branded envelope: gray page, 600px white card, header, body, footer."""
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
        f"EasyMoney &mdash; shared expenses, settled fairly.</div>"
        f"</div></div>"
    )


def _greeting(name: str | None) -> str:
    """The personal greeting line (the plain-text twin is built by callers)."""
    line = f"Hi {escape(name)}," if name else "Hi,"
    return (
        f'<p style="margin:0 0 12px;font-family:{_FONT};font-size:18px;font-weight:600;'
        f'color:{_STRONG};">{line}</p>'
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


def _total_row(amount: str) -> str:
    """The bold accent-colored total line under the owed list."""
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="width:100%;border-collapse:collapse;"><tr>'
        f'<td style="padding:12px 0;border-top:1px solid {_CARD_BORDER};'
        f'font-family:{_FONT};font-size:15px;font-weight:700;color:{_STRONG};">Total</td>'
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


async def send_mail(to: str, subject: str, text: str, html: str | None = None) -> None:
    """Send one message; log it instead when no SMTP host is configured."""
    settings = get_settings()
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


def _money(amount: Decimal, currency: str) -> str:
    """Render money with its currency code: ``12.50 USD``, ``1250 JPY``."""
    return f"{currency_service.format_money(amount, currency)} {currency}"


def invite_message(
    group_name: str, inviter_name: str, token: str, days: int
) -> tuple[str, str, str]:
    """Subject/text/html for an email invitation (§2.10)."""
    link = f"{get_settings().frontend_url.rstrip('/')}/invite/{token}"
    subject = f'{inviter_name} invited you to "{group_name}" on EasyMoney'
    text = (
        "Hi,\n\n"
        f'{inviter_name} invited you to join the group "{group_name}" on EasyMoney.\n\n'
        "Accept the invitation:\n"
        f"{link}\n\n"
        f"This invitation expires in {days} days.\n\n"
        "— EasyMoney"
    )
    body = (
        _greeting(None)
        + f'<p style="margin:0 0 8px;">{escape(inviter_name)} invited you to join the group '
        + f"<strong>{escape(group_name)}</strong> on EasyMoney.</p>"
        + _cta(link, "Accept the invitation")
        + f'<p style="margin:16px 0 0;font-size:13px;color:{_MUTED};">'
        + f"This invitation expires in {days} days.</p>"
    )
    return subject, text, _email_html(body)


def overdue_reminder_message(
    group_name: str,
    currency: str,
    debtor_name: str,
    per_creditor: list[tuple[str, Decimal]],
    total: Decimal,
    group_id: int | None = None,
) -> tuple[str, str, str]:
    """Subject/text/html for an overdue-debt reminder (§2.7 step 4).

    ``per_creditor`` lists each creditor the debtor currently owes with the
    pairwise amount (it sums to ``total``). ``group_id`` links the CTA to the
    group page; without it the CTA falls back to the app home page.
    """
    group_url = _group_url(group_id)
    total_str = _money(total, currency)
    subject = f'EasyMoney: you owe {total_str} in "{group_name}"'
    items = "\n".join(f"  - {name}: {_money(amount, currency)}" for name, amount in per_creditor)
    text = (
        f"Hi {debtor_name},\n\n"
        f'Your debt in the group "{group_name}" is overdue.\n\n'
        "You currently owe:\n"
        f"{items}\n\n"
        f"Total: {total_str}\n\n"
        f"Open the group in EasyMoney:\n{group_url}\n\n"
        "- EasyMoney"
    )
    rows = "".join(
        _money_row(escape(name), _money(amount, currency), last=(i == len(per_creditor) - 1))
        for i, (name, amount) in enumerate(per_creditor)
    )
    body = (
        _greeting(debtor_name)
        + '<p style="margin:0 0 8px;">Your debt in the group '
        + f"<strong>{escape(group_name)}</strong> is overdue.</p>"
        + _section("You currently owe")
        + rows
        + _total_row(total_str)
        + _cta(group_url, "Open the group in EasyMoney")
    )
    return subject, text, _email_html(body)


def close_summary_message(
    group_name: str,
    currency: str,
    balances: list[tuple[str, Decimal]],
    pairs: list[tuple[str, str, Decimal]],
    transfers: list[tuple[str, str, Decimal]],
    recipient_name: str | None = None,
    group_id: int | None = None,
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
    greeting = f"Hi {recipient_name}," if recipient_name else "Hi,"
    link_text = f"Open the group in EasyMoney:\n{group_url}\n\n"
    subject = f'EasyMoney: final summary for "{group_name}"'
    intro = (
        '<p style="margin:0 0 8px;">The group '
        + f"<strong>{escape(group_name)}</strong> has been closed. Final summary:</p>"
    )
    if not transfers:
        text = (
            f"{greeting}\n\n"
            f'The group "{group_name}" has been closed. Final summary:\n\n'
            "Everyone is settled up - no outstanding debts.\n\n"
            f"{link_text}"
            "- EasyMoney"
        )
        body = (
            _greeting(recipient_name)
            + intro
            + f'<div style="margin-top:8px;padding:12px 16px;background-color:{_OK_BG};'
            + f'border:1px solid {_OK_BORDER};border-radius:8px;color:{_OK_TEXT};font-size:14px;">'
            + "Everyone is settled up - no outstanding debts.</div>"
            + _cta(group_url, "Open the group in EasyMoney")
        )
        return subject, text, _email_html(body)
    balance_rows = "".join(
        _money_row(escape(name), _money(amount, currency), last=(i == len(balances) - 1))
        for i, (name, amount) in enumerate(balances)
    )
    pair_rows = "".join(
        _money_row(
            f"{escape(frm)} owes {escape(to)}", _money(amount, currency), last=(i == len(pairs) - 1)
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
        f"  - {frm} owes {to} {_money(amount, currency)}" for frm, to, amount in pairs
    )
    transfer_items = "\n".join(
        f"  - {frm} -> {to}: {_money(amount, currency)}" for frm, to, amount in transfers
    )
    text = (
        f"{greeting}\n\n"
        f'The group "{group_name}" has been closed. Final summary:\n\n'
        "Balances:\n"
        f"{balance_items}\n\n"
        "Who owes whom:\n"
        f"{pair_items}\n\n"
        "Suggested transfers (the minimal set that settles everything):\n"
        f"{transfer_items}\n\n"
        f"{link_text}"
        "- EasyMoney"
    )
    body = (
        _greeting(recipient_name)
        + intro
        + _section("Balances")
        + balance_rows
        + _section("Who owes whom")
        + pair_rows
        + _section("Suggested transfers")
        + transfer_rows
        + _cta(group_url, "Open the group in EasyMoney")
    )
    return subject, text, _email_html(body)
