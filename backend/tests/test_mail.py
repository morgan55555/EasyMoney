"""Mail templates: HTML escaping + icon header (PLAN WS3, fixes X-3).

The three builders are pure (subject/text/html from inputs), so these tests
need no database and no SMTP — they assert directly on the template output.

X-3: user-controlled values (group name, display names) once flowed into the
HTML bodies unescaped; a crafted group name rendered live
``<img src=x onerror=...>`` markup. The templates must now escape them in the
HTML body, while the subject and plain-text body stay raw (plain-text
context, documented in ``app/services/mail.py``). Every HTML body also
carries the app icon as a base64 data URI.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.core.config import get_settings
from app.services import mail

PROBE = '<img src=x onerror="alert(1)">'
ESCAPED_PROBE = "&lt;img src=x onerror=&quot;alert(1)&quot;"


def _assert_escaped_html(html: str, subject: str, text: str) -> None:
    """The common X-3 contract for all three templates."""
    # The probe must be inert in the HTML body: escaped, never live markup.
    assert ESCAPED_PROBE in html
    assert PROBE not in html
    # Subjects and plain text are plain-text context: raw values expected.
    assert PROBE in subject
    assert PROBE in text
    # Every HTML body carries the inlined icon (no external image request).
    assert "data:image/png;base64," in html


class TestInviteEscaping:
    def test_group_and_inviter_names_are_escaped(self) -> None:
        subject, text, html = mail.invite_message(
            group_name=PROBE, inviter_name=PROBE, token="sample-token", days=7
        )
        _assert_escaped_html(html, subject, text)
        # The invite link must remain functional (not mangled by escaping).
        assert "/invite/sample-token" in html


class TestReminderEscaping:
    def test_debtor_group_and_creditor_names_are_escaped(self) -> None:
        subject, text, html = mail.overdue_reminder_message(
            group_name=PROBE,
            currency="USD",
            debtor_name=PROBE,
            per_creditor=[(PROBE, Decimal("12.50")), ("Carol", Decimal("7.50"))],
            total=Decimal("20.00"),
        )
        _assert_escaped_html(html, subject, text)
        # A non-malicious creditor name stays readable (label cell + amount cell).
        assert "Carol</td>" in html
        assert "7.50 USD</td>" in html

    def test_amounts_carry_the_currency_code(self) -> None:
        subject, text, html = mail.overdue_reminder_message(
            group_name="Trip",
            currency="USD",
            debtor_name="Bob",
            per_creditor=[("Alice", Decimal("12.50"))],
            total=Decimal("12.50"),
        )
        assert "you owe 12.50 USD" in subject
        assert "Alice: 12.50 USD" in text
        assert "Total: 12.50 USD" in text
        assert "12.50 USD" in html

    def test_links_to_the_group(self) -> None:
        url = get_settings().frontend_url.rstrip("/")
        group_url = f"{url}/groups/7"
        _, text, html = mail.overdue_reminder_message(
            group_name="Trip",
            currency="USD",
            debtor_name="Bob",
            per_creditor=[("Alice", Decimal("1.00"))],
            total=Decimal("1.00"),
            group_id=7,
        )
        # The CTA (and the plain-text link) point at the group page.
        assert group_url in text
        assert f'href="{group_url}"' in html
        assert "Open the group in EasyMoney</a>" in html
        # Without a group id the link falls back to the app home page.
        _, text2, html2 = mail.overdue_reminder_message(
            group_name="Trip",
            currency="USD",
            debtor_name="Bob",
            per_creditor=[("Alice", Decimal("1.00"))],
            total=Decimal("1.00"),
        )
        assert f"Open the group in EasyMoney:\n{url}\n" in text2
        assert f'href="{url}"' in html2
        assert "/groups/" not in html2


class TestCloseSummaryEscaping:
    def test_names_in_balances_pairs_and_transfers_are_escaped(self) -> None:
        subject, text, html = mail.close_summary_message(
            group_name=PROBE,
            currency="USD",
            balances=[(PROBE, Decimal("-20.00"))],
            pairs=[(PROBE, "Alice", Decimal("20.00"))],
            transfers=[(PROBE, "Alice", Decimal("20.00"))],
        )
        _assert_escaped_html(html, subject, text)

    def test_settled_variant_is_escaped_too(self) -> None:
        # Empty transfers -> the "everyone is settled up" branch.
        subject, text, html = mail.close_summary_message(
            group_name=PROBE, currency="USD", balances=[], pairs=[], transfers=[]
        )
        _assert_escaped_html(html, subject, text)
        assert "settled up" in html

    def test_greets_the_recipient_by_name(self) -> None:
        subject, text, html = mail.close_summary_message(
            group_name="Trip",
            currency="USD",
            balances=[("Alice", Decimal("12.50"))],
            pairs=[("Bob", "Alice", Decimal("12.50"))],
            transfers=[("Bob", "Alice", Decimal("12.50"))],
            recipient_name="Alice",
        )
        assert text.startswith("Hi Alice,")
        assert ">Hi Alice,</p>" in html
        # Without a recipient name the greeting stays generic.
        _, text2, html2 = mail.close_summary_message(
            group_name="Trip",
            currency="USD",
            balances=[("Alice", Decimal("12.50"))],
            pairs=[("Bob", "Alice", Decimal("12.50"))],
            transfers=[("Bob", "Alice", Decimal("12.50"))],
        )
        assert text2.startswith("Hi,")
        assert ">Hi,</p>" in html2

    def test_amounts_carry_the_currency_code(self) -> None:
        _, text, html = mail.close_summary_message(
            group_name="Trip",
            currency="USD",
            balances=[("Alice", Decimal("12.50")), ("Bob", Decimal("-12.50"))],
            pairs=[("Bob", "Alice", Decimal("12.50"))],
            transfers=[("Bob", "Alice", Decimal("12.50"))],
        )
        assert "  - Alice: 12.50 USD" in text
        assert "  - Bob owes Alice 12.50 USD" in text
        assert "  - Bob -> Alice: 12.50 USD" in text
        # HTML renders the row as label + right-aligned amount cells.
        assert "Bob owes Alice</td>" in html
        assert "12.50 USD</td>" in html

    def test_links_to_the_group(self) -> None:
        url = get_settings().frontend_url.rstrip("/")
        group_url = f"{url}/groups/7"
        _, text, html = mail.close_summary_message(
            group_name="Trip",
            currency="USD",
            balances=[("Alice", Decimal("12.50"))],
            pairs=[("Bob", "Alice", Decimal("12.50"))],
            transfers=[("Bob", "Alice", Decimal("12.50"))],
            group_id=7,
        )
        # The CTA (and the plain-text link) point at the group page.
        assert group_url in text
        assert f'href="{group_url}"' in html
        assert "Open the group in EasyMoney</a>" in html
        # Without a group id the link falls back to the app home page.
        _, text2, html2 = mail.close_summary_message(
            group_name="Trip",
            currency="USD",
            balances=[("Alice", Decimal("12.50"))],
            pairs=[("Bob", "Alice", Decimal("12.50"))],
            transfers=[("Bob", "Alice", Decimal("12.50"))],
        )
        assert f"Open the group in EasyMoney:\n{url}\n" in text2
        assert f'href="{url}"' in html2
        assert "/groups/" not in html2


class TestBrandedEnvelope:
    """The HTML bodies are the app-styled envelope (§3.4): brand header with
    the inlined icon + EasyMoney wordmark, a 600px card, indigo CTA button."""

    def test_invite_has_brand_header_and_cta(self) -> None:
        _, _, html = mail.invite_message(
            group_name="Trip", inviter_name="Frank", token="sample-token", days=7
        )
        # 600px-wide card on the gray page.
        assert "max-width:600px" in html
        assert "background-color:#f9fafb" in html
        # Brand header: inlined icon + the two-tone wordmark, bold like the
        # app header.
        assert "data:image/png;base64," in html
        assert 'Easy<span style="color:#4f46e5;">Money</span>' in html
        assert "font-size:32px;font-weight:800" in html
        # The invite CTA is the indigo button linking the invite token URL.
        url = get_settings().frontend_url.rstrip("/")
        assert f'href="{url}/invite/sample-token"' in html
        assert "Accept the invitation</a>" in html

    def test_reminder_has_cta_button(self) -> None:
        _, _, html = mail.overdue_reminder_message(
            group_name="Trip",
            currency="USD",
            debtor_name="Bob",
            per_creditor=[("Alice", Decimal("1.00"))],
            total=Decimal("1.00"),
            group_id=7,
        )
        url = get_settings().frontend_url.rstrip("/")
        assert "background-color:#4f46e5" in html
        assert f'href="{url}/groups/7"' in html
        # The reminder CTA shares the summary's label.
        assert "Open the group in EasyMoney</a>" in html
        # Ruled rows: a line under the section title, between rows, and above
        # the total (gray-200, like the app).
        assert "border-bottom:1px solid #e5e7eb" in html
        assert "border-top:1px solid #e5e7eb" in html

    def test_settled_summary_has_ok_box(self) -> None:
        _, _, html = mail.close_summary_message(
            group_name="Trip", currency="USD", balances=[], pairs=[], transfers=[]
        )
        assert "settled up" in html
        assert "background-color:#ecfdf5" in html

    def test_style_attributes_survive_html_parsing(self) -> None:
        """Regression: the font stack once carried double quotes, which
        terminated the ``style="..."`` attributes and silently dropped every
        declaration after them (wordmark size, right-aligned amounts)."""
        invite = mail.invite_message("Trip", "Frank", "sample-token", 7)[2]
        reminder = mail.overdue_reminder_message(
            "Trip", "USD", "Bob", [("Alice", Decimal("1.00"))], Decimal("1.00")
        )[2]
        summary = mail.close_summary_message(
            "Trip",
            "USD",
            [("Alice", Decimal("12.50"))],
            [("Bob", "Alice", Decimal("12.50"))],
            [("Bob", "Alice", Decimal("12.50"))],
        )[2]
        # The full wordmark declaration fits inside one style attribute.
        for html in (invite, reminder, summary):
            assert re.search(r'style="[^"]*font-size:32px;font-weight:800[^"]*"', html)
        # ...and the amount alignment survives too.
        for html in (reminder, summary):
            assert re.search(r'style="[^"]*text-align:right[^"]*"', html)


def test_icon_data_uri_is_loaded_and_cached() -> None:
    first = mail._icon_data_uri()
    assert first.startswith("data:image/png;base64,")
    # The module-level cache returns the same object on every call.
    assert mail._icon_data_uri() is first
