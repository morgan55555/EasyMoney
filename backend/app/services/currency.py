"""Currency handling (AGENTS.md §2.12).

Each group has exactly one ISO 4217 currency. Money in the API is always a
string, formatted with the currency's minor digits. No conversion anywhere.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.core.errors import ValidationFailed

# Minor digits per currency; default is 2 (§2.12).
CURRENCY_MINOR_DIGITS: dict[str, int] = {
    "JPY": 0,
    "KRW": 0,
    "VND": 0,
    "KWD": 3,
    "BHD": 3,
    "OMR": 3,
    "TND": 3,
}

_CODE_RE = re.compile(r"^[A-Z]{3}$")


def minor_digits(code: str) -> int:
    return CURRENCY_MINOR_DIGITS.get(code, 2)


def validate_currency(value: str) -> str:
    """Normalize and validate an ISO 4217 code; raises ValidationFailed."""
    code = value.strip().upper()
    if not _CODE_RE.match(code):
        raise ValidationFailed("Currency must be a 3-letter ISO 4217 code (e.g. USD)")
    return code


def format_money(amount: Decimal, currency: str) -> str:
    """Render money as a string with the currency's minor digits (§2.12)."""
    quantum = Decimal(1).scaleb(-minor_digits(currency))
    return str(amount.quantize(quantum, rounding=ROUND_HALF_UP))


def to_minor(amount: Decimal, currency: str) -> int:
    """Convert a ``Decimal`` amount to minor units (int).

    Rejects non-finite values and amounts with digits finer than the
    currency's minor unit (e.g. ``1.999`` for a 2-digit currency) — such
    input is a 422. The precision check works on ``as_tuple()`` (no context
    arithmetic) and the ``quantize`` is guarded, so no input can raise
    ``decimal.InvalidOperation`` (H-7/H-14).
    """
    if not amount.is_finite():
        raise ValidationFailed("Amount must be a finite number")
    digits = minor_digits(currency)
    # Pre-check in the tuple representation: an exponent finer than the
    # currency's minor unit means the amount has too many decimal places
    # (covers sub-precision values like "-0.001" and "1e-7"). The exponent is
    # int for every finite value (the non-int spellings mean NaN/Inf, already
    # rejected above).
    exponent = amount.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -digits:
        raise ValidationFailed(
            f"Amount {amount} has more precision than {currency} allows "
            f"({digits} decimal place{'s' if digits != 1 else ''})"
        )
    quantum = Decimal(1).scaleb(-digits)
    try:
        rounded = amount.quantize(quantum, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        # Huge amounts whose quantized form exceeds the context precision.
        raise ValidationFailed(f"Amount {amount} is out of range for {currency}") from None
    if rounded != amount:
        raise ValidationFailed(
            f"Amount {amount} has more precision than {currency} allows "
            f"({digits} decimal place{'s' if digits != 1 else ''})"
        )
    return int(amount.scaleb(digits))


def from_minor(minor: int, currency: str) -> Decimal:
    """Convert minor units (int) back to a ``Decimal`` amount."""
    return Decimal(minor).scaleb(-minor_digits(currency))


def normalize_money(amount: Decimal, currency: str) -> Decimal:
    """Re-scale a *stored* amount to the currency's exact minor digits.

    Money columns are ``NUMERIC(14,3)`` (§3.7), so a 2-digit-currency value
    read back from the database carries scale 3 (``5.000``) and would trip
    ``to_minor``'s strict sub-precision check — which is there to reject
    user *input* finer than the minor unit, not canonical stored values.
    Quantizing restores the canonical spelling (``5.00``) without ever
    rejecting.
    """
    quantum = Decimal(1).scaleb(-minor_digits(currency))
    return amount.quantize(quantum, rounding=ROUND_HALF_UP)
